#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="/opt/nightowl/NightOwl.Agent.Windows/publish/downloads/agent/windows"
DEST_DIR="/opt/nightowl/downloads/agent/windows"
VALIDATION_BASE_URL="${VALIDATION_BASE_URL:-https://nightowl.control.local/downloads/nightowl-agent}"

REQUIRED_FILES=(
  "Install-NightOwlAgentDotNet.ps1"
  "NightOwl.Agent.Windows.zip"
  "Uninstall-NightOwlAgentDotNet.ps1"
  "NightOwl.ico"
  "checksums.json"
  "version.json"
)

log() {
  printf '[nightowl-agent-publish] %s\n' "$*"
}

fail() {
  printf '[nightowl-agent-publish] ERRO: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Comando obrigatorio nao encontrado: $1"
}

validate_safe_path() {
  local path="$1"
  local expected_prefix="$2"
  [[ -n "$path" ]] || fail "Caminho vazio nao permitido."
  [[ "$path" != "/" ]] || fail "Caminho raiz nao permitido."
  [[ "$path" == "$expected_prefix"* ]] || fail "Caminho fora do prefixo esperado: $path"
}

require_command rsync
require_command curl
require_command sha256sum

validate_safe_path "$SOURCE_DIR" "/opt/nightowl/NightOwl.Agent.Windows/publish/downloads/agent/windows"
validate_safe_path "$DEST_DIR" "/opt/nightowl/downloads/agent/windows"

[[ -d "$SOURCE_DIR" ]] || fail "Diretorio de origem nao existe: $SOURCE_DIR"

for file in "${REQUIRED_FILES[@]}"; do
  [[ -f "$SOURCE_DIR/$file" ]] || fail "Arquivo obrigatorio ausente na origem: $SOURCE_DIR/$file"
done

log "Criando destino se necessario: $DEST_DIR"
install -d -m 755 "$DEST_DIR"

log "Publicando arquivos com rsync --delete"
rsync -av --delete "$SOURCE_DIR/" "$DEST_DIR/"

log "Ajustando ownership para www-data:www-data"
chown -R www-data:www-data "$DEST_DIR"

log "Ajustando permissoes"
find "$DEST_DIR" -type d -exec chmod 755 {} +
find "$DEST_DIR" -type f -exec chmod 644 {} +

log "Listagem final"
ls -lah "$DEST_DIR"

log "Checksum SHA256 do ZIP publicado"
sha256sum "$DEST_DIR/NightOwl.Agent.Windows.zip"

log "Validando URLs publicas locais"
curl -kI "$VALIDATION_BASE_URL/Install-NightOwlAgentDotNet.ps1"
curl -kI "$VALIDATION_BASE_URL/NightOwl.Agent.Windows.zip"
curl -kI "$VALIDATION_BASE_URL/version.json"

log "Publicacao concluida. Nao foi necessario reiniciar nightowl.service."
