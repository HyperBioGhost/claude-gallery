#!/usr/bin/env bash
# Write the Claude Gallery instructions into a CLAUDE.md.
#   update-instructions.sh <CLAUDE.md> <claude-instructions.md>
#
# The block between the gallery markers is replaced where it sits, so the
# person's own sections keep their order; with no block yet it is added at the
# end, and an empty or missing file gets just the block. Run on every install,
# so an upgrade carries new rules. Plain bash and perl, so it is tested on any
# machine, not only a Mac.
set -euo pipefail
CLAUDE_MD=$1
BLOCK_FILE=$2
[ -r "$BLOCK_FILE" ] || { echo "cannot read $BLOCK_FILE" >&2; exit 1; }
mkdir -p "$(dirname "$CLAUDE_MD")"

if [ ! -s "$CLAUDE_MD" ]; then
  {
    printf '<!-- claude-gallery-start -->\n'
    awk 1 "$BLOCK_FILE" | perl -0777 -pe 's/\s+\z/\n/'
    printf '<!-- claude-gallery-end -->\n'
  } > "$CLAUDE_MD"
  exit 0
fi

BLOCK_FILE="$BLOCK_FILE" perl -0777 -pi -e '
  BEGIN {
    local $/; open my $f, "<", $ENV{BLOCK_FILE} or die "cannot read $ENV{BLOCK_FILE}: $!";
    $blk = <$f>; $blk =~ s/\s+\z//;
  }
  my $nl = /\r\n/ ? "\r\n" : "\n";          # follow the file, never mix endings
  (my $out = $blk) =~ s/\r?\n/$nl/g;
  $out = "<!-- claude-gallery-start -->$nl$out$nl<!-- claude-gallery-end -->";
  my $n = 0;
  s/<!-- claude-gallery-start -->.*?<!-- claude-gallery-end -->/$n++ ? "" : $out/gse;
  if (!$n) { s/\s+\z//; $_ .= "$nl$nl$out$nl"; }
' "$CLAUDE_MD"
