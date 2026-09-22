#!/usr/bin/env bash
#
# Turn a failed Gradle run's output into GitHub annotations.
#
# Why this exists: a workflow's step log is only readable by someone signed in
# with access to the repository, but annotations render on the run summary for
# anyone — including tools and people triaging from outside. Without this, the
# only way to learn why an Android build failed is for a human to open the log
# and copy it out by hand, which is slow and loses detail.
#
# It parses the three failure shapes this project actually produces:
#
#   Kotlin   e: file:///abs/path/Foo.kt:12:34 Unresolved reference: bar
#   AAPT     ERROR: /abs/path/res/values/x.xml:4: AAPT: error: ...
#   Gradle   * What went wrong: ... (the block that explains a task failure)
#
# Usage: ci-annotate-gradle.sh <logfile>

set -uo pipefail

log="${1:?usage: ci-annotate-gradle.sh <logfile>}"
[ -f "$log" ] || { echo "::warning::No Gradle log at $log"; exit 0; }

workspace="${GITHUB_WORKSPACE:-$PWD}"
summary="${GITHUB_STEP_SUMMARY:-/dev/null}"
# A broken build can produce hundreds of cascading errors; the first ones are
# the real ones, and flooding the run with annotations hides them.
MAX=40
count=0

emit() { # emit <file> <line> <col> <message>
    [ "$count" -ge "$MAX" ] && return
    count=$((count + 1))
    local f="${1#"$workspace"/}"
    printf '::error file=%s,line=%s,col=%s::%s\n' "$f" "$2" "$3" "$4"
}

{
    echo "## Build failure"
    echo
} >> "$summary"

# --- Kotlin and Java compiler errors ----------------------------------------
kotlin_errors=$(grep -E '^e: ' "$log" || true)
if [ -n "$kotlin_errors" ]; then
    echo "### Compiler errors" >> "$summary"
    echo '```' >> "$summary"
    while IFS= read -r line; do
        echo "$line" >> "$summary"
        # e: file:///path/Foo.kt:12:34 message
        if [[ "$line" =~ ^e:\ file://([^:]+):([0-9]+):([0-9]+)\ (.*)$ ]]; then
            emit "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" "${BASH_REMATCH[3]}" "${BASH_REMATCH[4]}"
        else
            emit "" 1 1 "${line#e: }"
        fi
    done <<< "$kotlin_errors"
    echo '```' >> "$summary"
fi

# --- Resource linking (AAPT) -------------------------------------------------
aapt_errors=$(grep -E '^(ERROR|error):.*(AAPT|aapt)' "$log" || true)
if [ -n "$aapt_errors" ]; then
    echo "### Resource errors" >> "$summary"
    echo '```' >> "$summary"
    while IFS= read -r line; do
        echo "$line" >> "$summary"
        if [[ "$line" =~ ([^[:space:]:]+):([0-9]+):\ .*AAPT:\ (.*)$ ]]; then
            emit "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" 1 "${BASH_REMATCH[3]}"
        else
            emit "" 1 1 "$line"
        fi
    done <<< "$aapt_errors"
    echo '```' >> "$summary"
fi

# --- Gradle's own explanation ------------------------------------------------
# Everything from "What went wrong" up to the "Try:" block that follows it.
went_wrong=$(awk '/^\* What went wrong:/{f=1} f{print} /^\* Try:/{if(f)exit}' "$log" | head -n 40)
if [ -n "$went_wrong" ]; then
    {
        echo "### Gradle"
        echo '```'
        echo "$went_wrong"
        echo '```'
    } >> "$summary"
    # One annotation carrying the first two lines, which name the failing task.
    first=$(echo "$went_wrong" | sed -n '2,3p' | tr '\n' ' ')
    [ -n "$first" ] && printf '::error::%s\n' "$first"
fi

if [ "$count" -eq 0 ]; then
    echo "::warning::The build failed but no recognised error lines were found; see the step log."
    { echo "_No recognised error lines. See the raw step log._"; } >> "$summary"
else
    echo "Surfaced $count error annotation(s)."
fi

if [ "$count" -ge "$MAX" ]; then
    echo "::warning::More than $MAX errors; only the first $MAX are annotated."
fi
