import unittest
from unittest.mock import Mock, patch

import requests

import main
from book_context import BookContext, BookUnavailableError
from weread_shelf import ShelfBook, ShelfSnapshot


class ShelfReadingTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = ShelfSnapshot([ShelfBook("101", "101"), ShelfBook("202", "202"), ShelfBook("303", "303")], 3, 0, 1, 0)
        self.cursor = ""
        self.order = []
        def one_book(client, printer, reference, count, **kwargs):
            self.order.append(reference)
            self.assertEqual(count, 1)
            self.assertFalse(kwargs["refresh_session"])
            self.assertFalse(kwargs["disclose"])
            self.assertEqual(kwargs["expected_book_id"], reference)
            return main.ReadingResult(reference, "Private book title", 1, 30)
        for target, options in (("main.BOOK_URL", {"new": ""}),
                                ("main.load_shelf", {"return_value": self.snapshot}),
                                ("main.reading_cursor", {"side_effect": lambda: self.cursor}),
                                ("main.save_reading_cursor", {"side_effect": lambda book_id: setattr(self, "cursor", book_id)}),
                                ("main.refresh_cookie", {"return_value": True}),
                                ("main.run_single_book", {"side_effect": one_book})):
            patcher = patch(target, **options)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_default_cycles_in_order_and_read_num_is_a_total_not_per_book(self):
        printer = Mock()
        with self.assertLogs(level="INFO") as logs:
            result = main.run_reading(Mock(), printer, count=5)
        self.assertEqual(self.order, ["101", "202", "303", "101", "202"])
        self.assertEqual((result.completed, result.acknowledged_seconds, result.books_read), (5, 150, 3))
        self.assertEqual(result.mode, "shelf")
        self.assertEqual(self.cursor, "303")
        self.assertNotIn("Private book title", "\n".join(logs.output))
        self.assertNotIn("101", str(printer.call_args_list))

    def test_next_run_continues_at_the_saved_next_book(self):
        self.cursor = "202"
        main.run_reading(Mock(), Mock(), count=2)
        main.run_reading(Mock(), Mock(), count=2)
        self.assertEqual(self.order, ["202", "303", "101", "202"])
        self.assertEqual(self.cursor, "303")

    def test_removed_cursor_book_restarts_from_first_available_book(self):
        self.cursor = "no-longer-in-shelf"
        main.run_reading(Mock(), Mock(), count=1)
        self.assertEqual(self.order, ["101"])

    def test_explicit_book_does_not_fetch_shelf_or_update_cursor(self):
        with patch("main.run_single_book", return_value=main.ReadingResult("25615385", "title", 2, 60)) as single, patch("main.load_shelf") as shelf:
            result = main.run_reading(Mock(), Mock(), reference="25615385", count=2)
        shelf.assert_not_called()
        self.assertEqual(single.call_args.args[-2:], ("25615385", 2))
        self.assertEqual(result.mode, "single")
        self.assertEqual(self.cursor, "")

    def test_unavailable_book_is_skipped_without_consuming_a_success_period(self):
        self.snapshot.books = self.snapshot.books[:2]
        with patch("main.run_single_book", side_effect=[BookUnavailableError("unavailable"), main.ReadingResult("202", "private", 1, 30)]) as single:
            result = main.run_reading(Mock(), Mock(), count=1)
        self.assertEqual(single.call_count, 2)
        self.assertEqual(result.completed, 1)
        self.assertEqual(self.cursor, "202")

    def test_all_unavailable_books_stop_instead_of_looping_forever(self):
        with patch("main.run_single_book", side_effect=BookUnavailableError("unavailable")) as single, self.assertRaises(RuntimeError):
            main.run_reading(Mock(), Mock(), count=1)
        self.assertEqual(single.call_count, 3)
        self.assertEqual(self.cursor, "")

    def test_auth_failure_does_not_skip_through_entire_shelf(self):
        error = requests.HTTPError("unauthorized")
        error.response = requests.Response()
        error.response.status_code = 401
        with patch("main.run_single_book", side_effect=error) as single, self.assertRaises(requests.HTTPError):
            main.run_reading(Mock(), Mock(), count=1)
        self.assertEqual(single.call_count, 1)


class PrivateBookLoggingTests(unittest.TestCase):
    def test_single_period_used_by_shelf_does_not_log_private_book_identity(self):
        context = BookContext("101", "Private shelf title", "https://weread.qq.com/web/reader/test", 172, 15, 0, 0, "private", "ps", "pc", "app", "token",
                              original={"chapterUid": 172, "chapterOffset": 0, "progress": 0})
        client = Mock()
        reply = requests.Response()
        reply.status_code = 200
        reply._content = b'{"succ":1,"synckey":1}'
        client.post.return_value = reply
        printer = Mock()
        with patch("main.load_context", return_value=context), patch("main.ProgressGuard"), patch("main.enter_book"), patch("main.wait_interval"), \
             patch("main.time.monotonic", side_effect=[0, 30, 30]), patch("main.logging.info") as info:
            main.run_single_book(client, printer, "101", 1, refresh_session=False, disclose=False, expected_book_id="101")
        self.assertNotIn(context.title, str(info.call_args_list) + str(printer.call_args_list))
        self.assertIn("私密书籍", str(printer.call_args_list))

    def test_title_cannot_inject_newlines_or_workflow_commands(self):
        self.assertEqual(main.safe_label("Title\n::error::\x1b[31m"), "Title ::error:: [31m")

    def test_public_book_has_shelf_index_and_title(self):
        snapshot = ShelfSnapshot([ShelfBook("101", "101", "Public title", False)], 1, 0, 0, 0)
        with patch("main.BOOK_URL", ""), patch("main.load_shelf", return_value=snapshot), \
             patch("main.reading_cursor", return_value=""), patch("main.save_reading_cursor"), patch("main.refresh_cookie"), \
             patch("main.run_single_book", return_value=main.ReadingResult("101", "Public title", 1, 30)) as read:
            with self.assertLogs(level="INFO") as logs:
                main.run_reading(Mock(), Mock(), count=1)
        self.assertIn("[1/1]《Public title》", "\n".join(logs.output))
        self.assertTrue(read.call_args.kwargs["disclose"])

    def test_progress_conflict_does_not_advance_shelf_cursor(self):
        from progress_guard import ProgressConflict
        snapshot = ShelfSnapshot([ShelfBook("101", "101")], 1, 0, 0, 0)
        with patch("main.BOOK_URL", ""), patch("main.load_shelf", return_value=snapshot), \
             patch("main.reading_cursor", return_value=""), patch("main.save_reading_cursor") as cursor, \
             patch("main.refresh_cookie"), patch("main.run_single_book", side_effect=ProgressConflict("phone")):
            with self.assertRaises(ProgressConflict):
                main.run_reading(Mock(), Mock(), count=1)
        cursor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
