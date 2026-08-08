from __future__ import annotations

import re

from libs.pluginbase import CommandFilterMatcher


class RegexCommandFilterMatcher(CommandFilterMatcher):
    name = "regex"

    def matches(self, command: str, patterns: list[str]) -> bool:
        return any(re.search(p, command) for p in patterns)
