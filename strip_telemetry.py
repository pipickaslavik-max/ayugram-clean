"""Zero-egress patch for AyuGram-iOS (mapix3/AyuGram-iOS, branch standard).

Every AyuGram telemetry/relay channel funnels through four constants plus two fetch
URLs; this script repoints them at a closed loopback port, so no byte of account,
build, profile, phone-lookup or AI data leaves the device. The CI's own checks keep
passing: they assert structure, not reachability.

    python3 strip_telemetry.py [repo-root]

Idempotent: already-patched files are reported and skipped. Exits non-zero when the
source has drifted so far that an anchor is missing -- do not force it then.
"""
import re
import sys
from pathlib import Path

LOOP = "https://127.0.0.1:1"

PATCHES = [
    # All AyuGram relay traffic: /stats/status beacon, /stats/ping, /stats/report,
    # /profile lookups + uploads, /badges, push relay, /ai assistant.
    ("Swiftgram/SGSimpleSettings/Sources/AyuGramSecurity.swift",
     'public static let relayURL = "https://relay.mapixdev.com"',
     f'public static let relayURL = "{LOOP}"'),
    # Build-integrity check that fetches releases with the installed build number.
    ("Swiftgram/SGSimpleSettings/Sources/AyuGramSecurity.swift",
     'public static let releaseURL = "https://github.com/mapix3/AyuGram-iOS/releases/download/build-"',
     f'public static let releaseURL = "{LOOP}/build-"'),
    # Bark push relay: endpoint carries subscription keys + Bark key, relay decrypts pushes.
    ("Swiftgram/SGSettingsUI/Sources/AyuGramPushRelay.swift",
     'static let baseURL = "https://relay.mapixdev.com"',
     f'static let baseURL = "{LOOP}"'),
    # Launch-time update manifest (GitHub sees IP + is the source of the block list).
    ("Swiftgram/SGSimpleSettings/Sources/AyuGramUpdates.swift",
     'public static let manifestURL = URL(string: "https://raw.githubusercontent.com/mapix3/AyuGram-iOS/standard/update.json")!',
     f'public static let manifestURL = URL(string: "{LOOP}/update.json")!'),
    # Swiftgram upstream GH settings fetch.
    ("Swiftgram/SGGHSettings/Sources/SGGHSettings.swift",
     'let baseURL = "https://raw.githubusercontent.com/Swiftgram/settings/refs/heads/main"',
     f'let baseURL = "{LOOP}/settings"'),
    # Swiftgram upstream localization download; bundled strings remain.
    ("Swiftgram/SGStrings/Sources/LocalizationManager.swift",
     'return "https://raw.githubusercontent.com/Swiftgram/Telegram-iOS/master/Swiftgram/SGStrings/Strings/\\(locale).lproj/SGLocalizable.strings"',
     f'return "{LOOP}/strings/\\(locale).lproj/SGLocalizable.strings"'),
    # Assistant providers: drop the relay default and the third-party free endpoint.
    # What stays talks only to the provider the user configured with their own key.
    # (marker: the surviving providers list)
    ("Swiftgram/SGSettingsUI/Sources/AyuGramAssistant.swift",
     '        AyuGramAssistantProvider(id: "ayugram", title: "AyuGram", endpoint: AyuGramPushRelay.baseURL + "/ai", model: "llama-3.3-70b", keyHint: "", needsKey: false),\n'
     '        AyuGramAssistantProvider(id: "pollinations", title: "Pollinations", endpoint: "https://text.pollinations.ai/openai", model: "openai", keyHint: "", needsKey: false),\n',
     '',
     'AyuGramAssistantProvider(id: "groq"'),
    # Swiftgram premium backend (purchases, bot web app). The CI build passes an
    # empty sg_config, so these struct defaults are the live addresses.
    ("Swiftgram/SGConfig/Sources/File.swift",
     '    public var apiUrl: String = "https://api.swiftgram.app"\n'
     '    public var webappUrl: String = "https://my.swiftgram.app"',
     f'    public var apiUrl: String = "{LOOP}"\n'
     f'    public var webappUrl: String = "{LOOP}"'),
]

# Literals the repository's own CI audit requires; if a patch ever removes one, the
# build would abort on the runner instead of shipping a broken app.
KEEP = [
    ("Swiftgram/SGSettingsUI/Sources/AyuGramPushRelay.swift",
     ['keys.count == 113', 'subdata(in: 32 ..< 97)', 'subdata(in: 97 ..< 113)',
      'apps.apple.com/app/id1403753865']),
]
BASEURL_CHECK = ("Swiftgram/SGSettingsUI/Sources/AyuGramPushRelay.swift",
                 r'baseURL = "https://[^"]+"')

FORBIDDEN = ['mapixdev', 'raw.githubusercontent.com', 'text.pollinations.ai',
             'api.swiftgram.app', 'my.swiftgram.app']


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else '.').resolve()
    if not (root / 'versions.json').is_file():
        print(f'error: {root} is not an AyuGram-iOS checkout (versions.json missing)')
        return 2

    applied = skipped = 0
    for patch in PATCHES:
        rel, old, new = patch[:3]
        marker = patch[3] if len(patch) > 3 else new
        path = root / rel
        text = path.read_text(encoding='utf-8')
        if marker and marker in text and old not in text:
            print(f'skip (already patched): {rel}')
            skipped += 1
            continue
        if text.count(old) != 1:
            print(f'error: anchor not found exactly once in {rel}')
            print('       source has drifted; re-audit before patching')
            return 1
        path.write_text(text.replace(old, new), encoding='utf-8')
        print(f'patched: {rel}')
        applied += 1

    failures = []
    for rel, needles in KEEP:
        text = (root / rel).read_text(encoding='utf-8')
        failures += [f'missing CI literal in {rel}: {n}' for n in needles if n not in text]
    rel, pattern = BASEURL_CHECK
    if not re.search(pattern, (root / rel).read_text(encoding='utf-8')):
        failures.append(f'CI requires baseURL = "https://..." in {rel}')

    leaks = []
    for path in (root / 'Swiftgram').rglob('*.swift'):
        text = path.read_text(encoding='utf-8', errors='ignore')
        for needle in FORBIDDEN:
            if needle in text:
                leaks.append(f'{path.relative_to(root)}: {needle}')
    failures += [f'still references {x}' for x in leaks]

    if failures:
        print('\n'.join('FAIL: ' + f for f in failures))
        return 1
    print(f'done: {applied} applied, {skipped} already patched; '
          f'no mapixdev / raw.githubusercontent / pollinations left in Swiftgram sources')
    return 0


if __name__ == '__main__':
    sys.exit(main())
