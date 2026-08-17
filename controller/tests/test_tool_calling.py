import json
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tool_calling import DEFAULT_MODEL, resolve_transcript, translate_tool_call


class TestTranslateToolCall(unittest.TestCase):

    def test_search(self):
        result = translate_tool_call("search", {"query": "Blue Monday New Order"})
        self.assertEqual(result, {"action": "search", "query": "Blue Monday New Order"})

    def test_download_by_index(self):
        result = translate_tool_call("download", {"index": 2})
        self.assertEqual(result, {"action": "download", "index": 2})

    def test_download_by_match(self):
        result = translate_tool_call("download", {"match": "blue monday"})
        self.assertEqual(result, {"action": "download", "match": "blue monday"})

    def test_download_index_takes_precedence_over_match(self):
        result = translate_tool_call("download", {"index": 3, "match": "blue monday"})
        self.assertEqual(result, {"action": "download", "index": 3})

    def test_download_missing_both_raises(self):
        with self.assertRaises(ValueError):
            translate_tool_call("download", {})

    def test_list_results(self):
        result = translate_tool_call("list_results", {})
        self.assertEqual(result, {"action": "list_results"})

    def test_unknown_tool_raises(self):
        with self.assertRaises(ValueError):
            translate_tool_call("delete_everything", {})


def _make_tool_use_block(name, tool_input):
    block = MagicMock()
    block.type = "tool_use"
    block.name = name
    block.input = tool_input
    return block


def _make_text_block(text):
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


class TestResolveTranscript(unittest.TestCase):

    def _mock_client(self, response_content):
        client = MagicMock()
        response = MagicMock()
        response.content = response_content
        client.messages.create.return_value = response
        return client

    def test_builds_request_with_model_tools_and_forced_tool_choice(self):
        client = self._mock_client(
            [_make_tool_use_block("list_results", {})]
        )

        resolve_transcript("what were the results", [], client=client)

        _, kwargs = client.messages.create.call_args
        self.assertEqual(kwargs["model"], DEFAULT_MODEL)
        self.assertEqual(
            kwargs["tool_choice"],
            {"type": "any", "disable_parallel_tool_use": True},
        )
        tool_names = {tool["name"] for tool in kwargs["tools"]}
        self.assertEqual(tool_names, {"search", "download", "list_results"})

    def test_transcript_and_last_results_appear_in_message_content(self):
        client = self._mock_client(
            [_make_tool_use_block("search", {"query": "Blue Monday"})]
        )
        last_results = [
            {"index": 1, "filename": "New Order - Blue Monday.flac", "user": "someuser"}
        ]

        resolve_transcript("search for blue monday", last_results, client=client)

        _, kwargs = client.messages.create.call_args
        message_content = kwargs["messages"][0]["content"]
        self.assertIn("search for blue monday", message_content)
        self.assertIn("Blue Monday.flac", message_content)

    def test_empty_last_results_shown_as_no_results(self):
        client = self._mock_client(
            [_make_tool_use_block("search", {"query": "test"})]
        )

        resolve_transcript("search for test", [], client=client)

        _, kwargs = client.messages.create.call_args
        message_content = kwargs["messages"][0]["content"]
        self.assertIn("No search results", message_content)

    def test_returns_translated_search_call(self):
        client = self._mock_client(
            [_make_tool_use_block("search", {"query": "Blue Monday New Order"})]
        )

        result = resolve_transcript("search for blue monday by new order", [], client=client)

        self.assertEqual(
            result, {"action": "search", "query": "Blue Monday New Order"}
        )

    def test_returns_translated_download_by_match(self):
        client = self._mock_client(
            [_make_tool_use_block("download", {"match": "blue monday"})]
        )

        result = resolve_transcript(
            "download the one that sounds like blue monday",
            [{"index": 1, "filename": "New Order - Blue Monday.flac"}],
            client=client,
        )

        self.assertEqual(result, {"action": "download", "match": "blue monday"})

    def test_no_tool_use_block_raises(self):
        client = self._mock_client([_make_text_block("I'm not sure what you mean.")])

        with self.assertRaises(RuntimeError):
            resolve_transcript("uh what", [], client=client)


if __name__ == "__main__":
    unittest.main()
