#!/usr/bin/env bash
# gen-checksums.sh -- generate/verify light/checksums.txt for pinned installs.
#
# setup-wsl-maude.ps1 -Release <tag> downloads light/checksums.txt from the tag
# and SHA-256-verifies every file in its $filesToDownload list. The list is
# parsed from the installer itself (the `Key = "..."` entries), so this stays
# in sync. Hashes are computed over the git BLOB bytes at <rev> (what
# raw.githubusercontent.com serves: LF for every file, including .ps1, since
# `eol=crlf` only affects the working tree), never over working-tree files
# (which may be CRLF-converted on Windows).
#
# Format (one line per file, sorted): <sha256>  <path>   (two spaces)
#
# Usage:
#   scripts/gen-checksums.sh [--check] [--rev REV] [--out FILE]
#     (default: write light/checksums.txt from HEAD)
#     --check  do not write; exit 1 if FILE is missing or differs from the
#              freshly computed content
set -euo pipefail

repo="$(git rev-parse --show-toplevel)"
rev="HEAD"
out="light/checksums.txt"
check=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --check) check=1 ;;
        --rev)   rev="${2:?--rev needs a value}"; shift ;;
        --out)   out="${2:?--out needs a value}"; shift ;;
        -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

cd "${repo}"
installer="light/setup-wsl-maude.ps1"

# Paths come from the installer's download list (Key = "<repo path>").
src="$(git show "${rev}:${installer}" | tr -d '\r')"
# One match per entry (an entry ends at its closing brace), so several entries
# on one line are all found.
mapfile -t entries < <(printf '%s\n' "${src}" \
    | grep -oE 'Url *= *"\$GH_RAW/[^"]*"[^}]*Key *= *"[^"]*"' || true)
mapfile -t paths < <(printf '%s\n' "${entries[@]}" \
    | sed -n 's/^.*Key *= *"\([^"]*\)"$/\1/p' \
    | LC_ALL=C sort -u)

if [[ ${#paths[@]} -eq 0 ]]; then
    echo "ERROR: no download entries found in ${installer}" >&2
    exit 1
fi

# Guard against a reformatted entry silently dropping a file: every
# `Url = "$GH_RAW/` occurrence must yield a key, and Url path must equal Key.
url_count="$(printf '%s\n' "${src}" | grep -oE 'Url *= *"\$GH_RAW/' | wc -l | tr -d ' ')"
if [[ "${url_count}" != "${#paths[@]}" || "${url_count}" != "${#entries[@]}" ]]; then
    echo "ERROR: installer has ${url_count} download Urls but ${#entries[@]} entries / ${#paths[@]} unique Keys (reformatted or duplicate entry?)" >&2
    exit 1
fi
while IFS= read -r m; do
    u="${m%%$'\t'*}"; k="${m#*$'\t'}"
    if [[ "${u}" != "${k}" ]]; then
        echo "ERROR: installer Url path '${u}' != Key '${k}'" >&2
        exit 1
    fi
done < <(printf '%s\n' "${entries[@]}" \
    | sed -n 's/^Url *= *"\$GH_RAW\/\([^"?]*\)[^"]*"[^}]*Key *= *"\([^"]*\)"$/\1\t\2/p')

content=""
for p in "${paths[@]}"; do
    if ! git cat-file -e "${rev}:${p}" 2>/dev/null; then
        echo "ERROR: ${p} is not tracked at ${rev}" >&2
        exit 1
    fi
    h="$(git cat-file blob "${rev}:${p}" | sha256sum | cut -d' ' -f1)"
    content+="${h}  ${p}"$'\n'
done

if [[ ${check} -eq 1 ]]; then
    if [[ ! -f "${out}" ]]; then
        echo "ERROR: ${out} is missing" >&2
        exit 1
    fi
    if [[ "$(tr -d '\r' < "${out}")" != "${content%$'\n'}" ]]; then
        echo "ERROR: ${out} is stale; run scripts/gen-checksums.sh and commit" >&2
        exit 1
    fi
    echo "${out} is up to date (${#paths[@]} files)"
    exit 0
fi

printf '%s' "${content}" > "${out}"
echo "Wrote ${out} (${#paths[@]} files, rev ${rev})"
