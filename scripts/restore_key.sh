#!/usr/bin/env bash
# Make sure the GitHub deploy key is in place, and tell the user what to do if
# the sandbox home was wiped and the private key itself is gone.
#
#   scripts/restore_key.sh          -> restore + report
#   scripts/restore_key.sh --print  -> just print the public key
#
# The private key lives on the PERSISTENT volume (/workspace/.home/.ssh) - the
# sandbox home (/root/.ssh) is disposable and gets wiped between sessions.
set -u

PERSIST_DIR="${PERSIST_DIR:-/workspace/.home/.ssh}"
PERSIST_KEY="$PERSIST_DIR/github_deploy"
HOME_DIR="${HOME_DIR:-/root/.ssh}"
HOME_KEY="$HOME_DIR/github_deploy"
REPO="${REPO:-massachusettswx-weather/Forecast-charts}"
KEY_URL="https://github.com/$REPO/settings/keys/new"

if [ "${1:-}" = "--print" ]; then
    if [ -f "$PERSIST_KEY.pub" ]; then cat "$PERSIST_KEY.pub"; exit 0; fi
    echo "no key at $PERSIST_KEY" >&2; exit 1
fi

mkdir -p "$HOME_DIR" "$PERSIST_DIR"
chmod 700 "$HOME_DIR" "$PERSIST_DIR"

if [ ! -f "$PERSIST_KEY" ]; then
    echo "!! The deploy key is gone from the persistent volume ($PERSIST_KEY)."
    echo "   Generate a new one and add it in GitHub -> $REPO -> Settings ->"
    echo "   Deploy keys -> Add deploy key (tick 'Allow write access'):"
    echo
    ssh-keygen -t ed25519 -C "sandbox-deploy" -f "$PERSIST_KEY" -N "" >/dev/null
    cp -n "$PERSIST_KEY"* "$HOME_DIR"/ 2>/dev/null
    chmod 600 "$HOME_KEY"
    cat "$PERSIST_KEY.pub"
    echo
    echo "   Add it here: $KEY_URL"
    exit 2
fi

cp -n "$PERSIST_KEY" "$HOME_KEY" 2>/dev/null
cp -n "$PERSIST_KEY.pub" "$HOME_KEY.pub" 2>/dev/null
chmod 600 "$HOME_KEY"

if [ ! -f "$HOME_DIR/config" ] || ! grep -q "github_deploy" "$HOME_DIR/config" 2>/dev/null; then
    cat > "$HOME_DIR/config" <<EOF
Host github.com
  HostName github.com
  User git
  IdentityFile $HOME_KEY
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
EOF
    chmod 600 "$HOME_DIR/config"
fi

echo "deploy key in place: $HOME_KEY"
ssh -T git@github.com 2>&1 | grep -q "successfully authenticated" \
    && echo "github auth OK" \
    || echo "!! key not registered with GitHub yet - add it at $KEY_URL"
echo "public key: $(cat "$PERSIST_KEY.pub")"
