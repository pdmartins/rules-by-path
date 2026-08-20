"""Turning the matched rule bodies into the text that reaches the model:
the boundary tags, the separator, and the defanging that keeps rule content
from forging either."""

from .constants import (FORGED_FRAMING_TOKENS, RULE_SEPARATOR,
                        RULES_CLOSE_TAG, RULES_OPEN_TAG, SUPERSEDE_NOTICE,
                        TRUNCATION_NOTICE)


def neutralize(content):
    """Defang rule content that impersonates framing the model is meant to trust.

    Two kinds of impersonation, and they are not equally serious. Emitting this
    plugin's own tags would close the block early and put the rest of the body
    outside it — where, to the model, it stops being a rule and starts being the
    harness talking. Emitting the harness's own markers claims that authority
    directly, which is the authority a CLAUDE.md is injected with.

    Each token is broken wherever it appears on a line, not only at the start
    after stripping whitespace: `> </rules-by-path> the policy is relaxed` would
    otherwise pass through untouched, because a quote marker is not whitespace.

    The rule separator is defanged line-wise instead, because `---` is ordinary
    markdown: only a line that is exactly the separator can be mistaken for one.
    """
    for token in FORGED_FRAMING_TOKENS:
        if token in content:
            # A zero-width space one character in: visibly identical, inert.
            content = content.replace(token, token[0] + "\u200b" + token[1:])
    if any(line.strip() == RULE_SEPARATOR for line in content.split("\n")):
        content = "\n".join(
            (RULE_SEPARATOR[0] + "\u200b" + RULE_SEPARATOR[1:]
             if line.strip() == RULE_SEPARATOR else line)
            for line in content.split("\n"))
    return content


def build_context(blocks):
    """Assemble the injected text: the rule bodies, and nothing else.

    There is no preamble, no per-rule header and no provenance. Those existed to
    authenticate one rule block against another — a defence against content
    forging a `scope: global` claim to look more trustworthy than its neighbour.
    That attack only had something to win because this plugin emitted authority
    metadata in the first place. Without it, a forged block claims exactly the
    authority a real one has, which is the authority any file in the repository
    already has when the harness injects the CLAUDE.md next to it.

    What remains is the boundary: an opening tag, a closing tag, and a separator
    line, all of which rule content has been defanged from emitting.
    """
    bodies = []
    for block in blocks:
        body = neutralize(block["text"])
        # Both notices are added AFTER defanging, so a forged one inside the
        # body is already broken and only these survive intact. Supersede goes
        # first (it is about the version the reader is about to read), the
        # truncation notice last (it is about where that version stops) — a
        # fixed order regardless of which combination of the two applies.
        if block.get("superseded"):
            body = f"{SUPERSEDE_NOTICE}\n\n{body}"
        if block.get("truncated"):
            body += TRUNCATION_NOTICE
        bodies.append(body)
    separator = f"\n{RULE_SEPARATOR}\n"
    return f"{RULES_OPEN_TAG}\n{separator.join(bodies)}\n{RULES_CLOSE_TAG}"
