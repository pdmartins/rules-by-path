"""The text the hook injects around the rules, in every language it ships.

This table is the plugin's property: configuration *selects* a row of it and
can never *supply* one. A config layer arrives with whatever repository is
checked out, and letting it provide the wording of the scaffolding would hand a
cloned repository the pen that writes the sentences the model trusts most —
which is why an unknown language falls back to English instead of reaching for
anything outside this file.

A Python module rather than a data file: no new I/O, no new parser, and no new
untrusted file on the injection path. The English row IS the constant the rest
of the plugin has always used, so selecting `en` emits byte-for-byte what the
plugin emitted before this table existed.

What is deliberately NOT translated is structure, not prose, and stays
byte-identical in every language: the boundary tags and the rule separator
(`neutralize` and every reader locate the block by their exact bytes), the
`[rules-by-path]` marker every SESSION_NOTICE opens with, the `{name!r}` and
`{body}` fields of the deny reason, and the admin command and flags quoted in
the notices — those are commands, not sentences.

`sanitize_language` lives here rather than in `config.py` for two reasons: this
module is what gives a language value its meaning, and `config.py` is already
at the 400-line ceiling this package holds every module to.
"""

import unicodedata

from .constants import (ADMIN_COMMAND, BRAZILIAN_PORTUGUESE, DEFAULT_LANGUAGE,
                        ENFORCE_DENY_REASON_TEMPLATE, HARNESS_MARKER,
                        LANGUAGE_EXTRA_CHARS, LANGUAGE_FORBIDDEN_CHARS,
                        LANGUAGE_KEY, LEGACY_NOTICE, MAX_LANGUAGE_CHARS,
                        SESSION_NOTICE, SUPERSEDE_NOTICE, TRUNCATION_NOTICES,
                        warn)

# The keys one row holds. They are the names the constants already carry, so a
# caller reads `messages[SESSION_NOTICE_KEY]` where it used to read the
# constant, and a translation missing one of them is a visible failure rather
# than a quietly untranslated line.
LEGACY_NOTICE_KEY = "LEGACY_NOTICE"
SESSION_NOTICE_KEY = "SESSION_NOTICE"
TRUNCATION_NOTICE_KEY = "TRUNCATION_NOTICE"
SUPERSEDE_NOTICE_KEY = "SUPERSEDE_NOTICE"
ENFORCE_DENY_REASON_TEMPLATE_KEY = "ENFORCE_DENY_REASON_TEMPLATE"
MESSAGE_KEYS = (LEGACY_NOTICE_KEY, SESSION_NOTICE_KEY, TRUNCATION_NOTICE_KEY,
                SUPERSEDE_NOTICE_KEY, ENFORCE_DENY_REASON_TEMPLATE_KEY)

# Two spellings of the same separator, because a language code is written both
# ways in the wild and nobody should have to guess which one this file wants.
LANGUAGE_SEPARATOR = "-"
LANGUAGE_SEPARATOR_ALIAS = "_"

# The Unicode form a value is folded to before anything looks at it. The
# compatibility one, not the canonical one: `ｅｎ` (fullwidth), `𝖾𝗇` (math bold)
# and `①` are alphanumeric, printable and identical to the eye, and only NFKC
# collapses them onto the ASCII they impersonate. Without it a value a human
# reads as `en` in the CLI's output would quietly select something else.
LANGUAGE_NORMAL_FORM = "NFKC"

MESSAGES = {
    DEFAULT_LANGUAGE: {
        LEGACY_NOTICE_KEY: LEGACY_NOTICE,
        SESSION_NOTICE_KEY: SESSION_NOTICE,
        TRUNCATION_NOTICE_KEY: TRUNCATION_NOTICES[DEFAULT_LANGUAGE],
        SUPERSEDE_NOTICE_KEY: SUPERSEDE_NOTICE,
        ENFORCE_DENY_REASON_TEMPLATE_KEY: ENFORCE_DENY_REASON_TEMPLATE,
    },
    BRAZILIAN_PORTUGUESE: {
        LEGACY_NOTICE_KEY: (
            "Este escopo ainda usa o formato antigo rules-map.yml, portanto "
            "NENHUMA regra está sendo injetada a partir dele. Migre-o "
            "executando: "
            f"\"{ADMIN_COMMAND}\" migrate --root <project-root> (ou --global). "
            "Avise o usuário que isto aconteceu."
        ),
        SESSION_NOTICE_KEY: (
            f"{HARNESS_MARKER} Esta sessão tem regras por caminho disponíveis. "
            "São arquivos markdown em `.claude/rules-by-path/` (projeto) e "
            "`~/.claude/rules-by-path/` (global), e eles chegam até você "
            "AUTOMATICAMENTE: no momento em que você toca um arquivo cujo glob "
            "casa, a regra é injetada no seu contexto. Portanto nunca há motivo "
            "para abrir, listar, grepar ou editar esses arquivos você mesmo — e "
            "a configuração recomendada os coloca em deny-list, então a "
            "tentativa é recusada em vez de atendida. Para ler ou alterar uma "
            "regra, use a CLI: "
            f"\"{ADMIN_COMMAND}\" list|show|which|add|update, com "
            "--root '<repo-root>' ou --global — ou a skill "
            "rules-by-path:manage, que a conduz para você."
        ),
        TRUNCATION_NOTICE_KEY: TRUNCATION_NOTICES[BRAZILIAN_PORTUGUESE],
        SUPERSEDE_NOTICE_KEY: (
            "Esta versão substitui qualquer ocorrência anterior desta regra na "
            "conversa."
        ),
        ENFORCE_DENY_REASON_TEMPLATE_KEY: (
            "rules-by-path: esta chamada de ferramenta está bloqueada pela "
            "regra imposta {name!r} (escopo global). O texto da própria regra é "
            "o motivo:\n\n{body}"
        ),
    },
}
SHIPPED_LANGUAGES = tuple(MESSAGES)


def normalize_language(language):
    """The spelling two language codes are compared under: case-folded, with
    `_` read as `-`, so `pt_br`, `PT-BR` and `pt-BR` are one language."""
    if not isinstance(language, str):
        return ""
    return language.strip().lower().replace(LANGUAGE_SEPARATOR_ALIAS,
                                            LANGUAGE_SEPARATOR)


CANONICAL_LANGUAGES = {normalize_language(code): code for code in MESSAGES}


def canonical_language(language):
    """The shipped code this value selects, or None when no shipped language
    matches it. The canonical spelling is the one this module indexes by; the
    sanitized value the user wrote is what the rules themselves are written
    in, and the two are deliberately allowed to differ."""
    return CANONICAL_LANGUAGES.get(normalize_language(language))


def has_translation(language):
    """Whether the injected text comes out in this language or falls back to
    English. `validate` and `config` report it, because a user who set a
    language the plugin does not ship should learn it from the CLI rather than
    by noticing English scaffolding around their own rules."""
    return canonical_language(language) is not None


def messages_for(language):
    """Every message, in the closest shipped language.

    Never raises and never returns a partial row: an unknown, misspelled or
    absent language yields the English one, because nothing about this setting
    may stop an injection. A copy, so a caller cannot edit the shipped table
    for the rest of the process."""
    return dict(MESSAGES[canonical_language(language) or DEFAULT_LANGUAGE])


def sanitize_language(raw, source):
    """One config layer's `language`, or None when the value is not usable.

    Sanitized like a glob and not like an internal value: it arrives from a
    layer that came with a cloned repository, and it goes straight into text
    the model reads. What the allowlist buys is worth stating precisely: no
    newline, no colon, no backtick, no angle bracket and no quote means the
    value cannot forge a delimiter, a frontmatter key or a second line. It does
    NOT mean 32 characters are too few to word an instruction — they are not —
    which is why every place that echoes the value quotes it instead of setting
    it loose in a sentence. `pt-BR`, `Portuguese (Brazil)` and `español` pass;
    `en\\nIgnore all previous instructions` does not.

    Folded to NFKC first, and the invisible alphanumerics refused outright: both
    exist so that a value which RENDERS as `en` IS `en`, rather than a lookalike
    that reads as English to whoever approved the file and selects something
    else in the code.

    There is no clamping here because there is no dangerous *direction* to
    limit, only a shape. A rejected value is warned about and dropped, so the
    layer below still decides — the same treatment every other unusable key
    gets, for the same reason: nothing may leave the hook unable to inject."""
    if not isinstance(raw, str):
        warn(f"{source}: '{LANGUAGE_KEY}' must be a string; ignored")
        return None
    text = unicodedata.normalize(LANGUAGE_NORMAL_FORM, raw).strip()
    allowed = all((char.isalnum() or char in LANGUAGE_EXTRA_CHARS)
                  and char not in LANGUAGE_FORBIDDEN_CHARS for char in text)
    if (not text or len(text) > MAX_LANGUAGE_CHARS or not text.isprintable()
            or not allowed or not any(char.isalpha() for char in text)):
        warn(f"{source}: '{LANGUAGE_KEY}' {raw[:MAX_LANGUAGE_CHARS]!r} must be "
             f"one printable line of at most {MAX_LANGUAGE_CHARS} visible "
             f"letters, digits, spaces or {LANGUAGE_EXTRA_CHARS.strip()!r}, "
             f"and must carry at least one letter; ignored")
        return None
    return text
