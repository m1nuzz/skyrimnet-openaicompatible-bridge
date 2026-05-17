"""Pure-unit regression tests for server.py.

These tests do not start the HTTP server, do not hit any network, and do not
require API keys -- they exercise the four hot spots from Fix.md that used to
silently corrupt user-visible output:

  * ``TagStripper`` -- split-tag handling across SSE chunks
  * ``safe_fix_mojibake`` -- mojibake repair that must not destroy clean text
  * ``is_leakage_line`` -- immersion filter must not eat numbered lists / ACTION:
  * ``is_json_task`` -- structural tasks must be detectable from request_json

The tests run on CI (.github/workflows/ci.yml) on every push and PR.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from server import (  # noqa: E402
    TagStripper,
    is_json_task,
    is_leakage_line,
    safe_fix_mojibake,
)

# --------------------------------------------------------------------- TagStripper


def _feed_chunks(chunks):
    ts = TagStripper()
    out = ""
    for c in chunks:
        out += ts.feed(c)
    out += ts.flush()
    return out


class TestTagStripper:
    def test_plain_passthrough(self):
        assert _feed_chunks(["plain text only"]) == "plain text only"

    def test_in_one_chunk(self):
        assert _feed_chunks(["hello <thought>secret</thought> world"]) == "hello  world"

    def test_thinking_variant(self):
        assert _feed_chunks(["a <thinking>b</thinking>c"]) == "a c"

    def test_split_open_tag(self):
        # The "<tho" + "ught>" split used to leak the entire reasoning to the
        # player.
        assert (
            _feed_chunks(["hello <tho", "ught>secret</thought> world"])
            == "hello  world"
        )

    def test_split_close_tag(self):
        # Closing tag straddling a chunk boundary used to stick the stream in
        # is_thinking=True forever and drop all subsequent text.
        assert _feed_chunks(["<thought>secret</thou", "ght>visible"]) == "visible"

    def test_unterminated_thought_dropped_on_flush(self):
        # Prefix is preserved; the never-closed thought body is dropped.
        assert _feed_chunks(["hello <thought>this never closes"]) == "hello "

    def test_cyrillic_preserved(self):
        assert (
            _feed_chunks(["Привет <thought>тайна</thought> мир"]) == "Привет  мир"
        )

    def test_multiple_thoughts(self):
        assert _feed_chunks(
            ["A <thought>x</thought>B <thought>y</thought>C"]
        ) == "A B C"

    def test_character_by_character(self):
        # Worst-case fragmentation: one char per chunk.
        src = "pre <thought>hidden</thought> post"
        assert _feed_chunks(list(src)) == "pre  post"

    def test_empty_feed(self):
        ts = TagStripper()
        assert ts.feed("") == ""
        assert ts.flush() == ""

    def test_no_carryover_at_end_of_safe_chunk(self):
        # A chunk that ends mid-potential-tag should hold the tail back.
        ts = TagStripper()
        out1 = ts.feed("hello <")
        # "<" alone could become "<thought>" -- must not be emitted yet.
        assert "<" not in out1
        out2 = ts.feed("thought>secret</thought> world")
        out3 = ts.flush()
        assert out1 + out2 + out3 == "hello  world"


# --------------------------------------------------------------------- mojibake


class TestSafeFixMojibake:
    def test_none_returns_none(self):
        assert safe_fix_mojibake(None) is None

    def test_empty_returns_empty(self):
        assert safe_fix_mojibake("") == ""

    def test_non_string_returned_unchanged(self):
        assert safe_fix_mojibake(42) == 42

    def test_clean_cyrillic_untouched(self):
        # The previous implementation destroyed this with errors='replace'.
        s = "Привет, как дела?"
        assert safe_fix_mojibake(s) == s

    def test_pure_mojibake_repaired(self):
        moji = "Привет".encode().decode("latin-1")
        assert safe_fix_mojibake(moji) == "Привет"

    def test_mixed_payload_left_unchanged(self):
        # Strict latin-1 encode raises on the clean prefix, so we must fall
        # back to the original rather than half-repairing.
        mixed = "Привет, " + "Привет".encode().decode("latin-1")
        assert safe_fix_mojibake(mixed) == mixed

    def test_data_uri_untouched(self):
        # base64 data URIs can contain bytes that look like mojibake; never
        # touch them.
        s = "data:image/png;base64,Ð\x9fXYZ"
        assert safe_fix_mojibake(s) == s

    def test_no_mojibake_signature_passthrough(self):
        assert safe_fix_mojibake("Just ASCII here.") == "Just ASCII here."

    def test_repair_introducing_replacement_char_is_rejected(self):
        # If the strict decode happens to succeed but introduces U+FFFD, we
        # should fall back to the original.
        # Construct a string whose decoded form would contain U+FFFD: take
        # raw bytes \xef\xbf\xbd (U+FFFD in utf-8) decoded as latin-1 and
        # apply the mojibake signature.
        garbled = "Ð\u00a0" + "\ufffd"
        # The fallback path must return the original on U+FFFD presence.
        result = safe_fix_mojibake(garbled)
        # Either unchanged (fallback) or no U+FFFD; never silently lossy.
        assert result == garbled or "\ufffd" not in result


# --------------------------------------------------------------------- leakage


class TestIsLeakageLine:
    @pytest.mark.parametrize(
        "line",
        [
            "Character: Aela the Huntress",
            "Setting: Tavern in Whiterun",
            "Thought: I wonder if she suspects me.",
            "Note to self: bring more arrows.",
            "Реакция: задумывается",
            "1. **Aela** -- doesn't trust the Companions.",
        ],
    )
    def test_leakage_detected(self, line):
        assert is_leakage_line(line)

    @pytest.mark.parametrize(
        "line",
        [
            "",
            "   ",
            "ACTION: Attack(Player)",
            "action: attack(player)",
            "1. Iron sword",
            "2. Health potion",
            "Note this down for later.",
            "Option pricing is tough.",
            "Setting up the kitchen takes hours.",
            "Plain dialogue with no colon.",
        ],
    )
    def test_not_leakage(self, line):
        assert not is_leakage_line(line)

    def test_skip_flag_bypasses(self):
        # When the request is a structural task, skip=True must short-circuit.
        assert not is_leakage_line("Character: Aela", skip=True)


# --------------------------------------------------------------------- json task


class TestIsJsonTask:
    def test_response_format_json_object(self):
        assert is_json_task({"response_format": {"type": "json_object"}}, "")

    def test_response_format_json_schema(self):
        assert is_json_task({"response_format": {"type": "json_schema"}}, "")

    def test_response_format_text_is_not_a_signal(self):
        assert not is_json_task(
            {"response_format": {"type": "text"}}, "normal dialogue"
        )

    def test_tools_list(self):
        assert is_json_task({"tools": [{"type": "function"}]}, "")

    def test_empty_tools_list_is_not_a_signal(self):
        assert not is_json_task({"tools": []}, "normal dialogue")

    def test_tool_choice_auto(self):
        assert is_json_task({"tool_choice": "auto"}, "")

    def test_tool_choice_required(self):
        assert is_json_task({"tool_choice": "required"}, "")

    def test_tool_choice_none(self):
        assert not is_json_task({"tool_choice": "none"}, "normal dialogue")

    def test_keyword_indicator(self):
        assert is_json_task({}, "Respond with ONLY a JSON object")

    def test_plain_dialogue(self):
        assert not is_json_task({}, "Hello there, friend.")

    def test_non_dict(self):
        assert not is_json_task("not a dict", "")
        assert not is_json_task(None, "")
