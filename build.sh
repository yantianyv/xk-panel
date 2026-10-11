#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FNOS_DIR="$SCRIPT_DIR/fnos"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# 1. Validate
info "检查插件文件..."
[ -d "$FNOS_DIR/cmd" ]    || error "缺少 fnos/cmd/"
[ -d "$FNOS_DIR/config" ] || error "缺少 fnos/config/"
[ -d "$FNOS_DIR/ui" ]     || error "缺少 fnos/ui/"
[ -d "$FNOS_DIR/app" ]    || error "缺少 fnos/app/"
[ -f "$FNOS_DIR/manifest" ]      || error "缺少 fnos/manifest"
[ -f "$FNOS_DIR/ICON.PNG" ]      || error "缺少 fnos/ICON.PNG"
[ -f "$FNOS_DIR/ICON_256.PNG" ]  || error "缺少 fnos/ICON_256.PNG"

APPNAME=$(grep "^appname" "$FNOS_DIR/manifest" | awk -F'=' '{print $2}' | tr -d ' ')
VERSION=$(grep "^version" "$FNOS_DIR/manifest" | awk -F'=' '{print $2}' | tr -d ' ')
PLATFORM=$(grep "^platform" "$FNOS_DIR/manifest" | awk -F'=' '{print $2}' | tr -d ' ')
[ -z "$APPNAME" ] && error "manifest 中缺少 appname"
[ -z "$VERSION" ] && error "manifest 中缺少 version"
info "应用: $APPNAME  版本: $VERSION  平台: ${PLATFORM:-x86}"

# 2. Create app.tgz
info "打包 app.tgz ..."
WORK_DIR="$SCRIPT_DIR/.build_tmp"
rm -rf "$WORK_DIR" 2>/dev/null || true
mkdir -p "$WORK_DIR"
trap 'rm -rf "$WORK_DIR" 2>/dev/null || :' EXIT
APP_ROOT="$WORK_DIR/app_root"
mkdir -p "$APP_ROOT"
cp -a "$FNOS_DIR/app" "$APP_ROOT/"
if [ -d "$FNOS_DIR/bin" ]; then cp -a "$FNOS_DIR/bin" "$APP_ROOT/"; chmod +x "$APP_ROOT/bin/"*; fi
cp -a "$FNOS_DIR/ui" "$APP_ROOT/"
[ -f "$FNOS_DIR/ICON_256.PNG" ] && cp "$FNOS_DIR/ICON_256.PNG" "$APP_ROOT/ui/images/256.png"
( cd "$APP_ROOT" && tar -czf "$WORK_DIR/app.tgz" . )
APP_TGZ="$WORK_DIR/app.tgz"
CHECKSUM=$(md5sum "$APP_TGZ" 2>/dev/null | cut -d' ' -f1 || md5 -q "$APP_TGZ")

# 3. Assemble fpk
info "组装 fpk ..."
PKG_DIR="$WORK_DIR/package"
mkdir -p "$PKG_DIR/cmd"
cp "$APP_TGZ" "$PKG_DIR/app.tgz"

# Inline shared/cmd/common (lifecycle framework)
cat > "$PKG_DIR/cmd/common" << 'SHARED_COMMON'
#!/bin/bash
MV="/bin/mv -f"; RM="/bin/rm -rf"; CP="/bin/cp -rfp"; MKDIR="/bin/mkdir -p"
LN="/bin/ln -nsf"; TEE="/usr/bin/tee -a"; RSYNC="/bin/rsync -avh"; TAR="/bin/tar"
if [ -z "${TRIM_PKGVAR:-}" ]; then echo "ERROR: TRIM_PKGVAR 未设置" >&2; exit 1; fi
case "${TRIM_PKGVAR}" in /vol*) ;; *) echo "ERROR: TRIM_PKGVAR=${TRIM_PKGVAR} 不在数据卷上" >&2; exit 1 ;; esac
/bin/mkdir -p "${TRIM_PKGVAR}" 2>/dev/null || true
INST_ETC="/var/apps/${TRIM_APPNAME}/etc"; INST_VARIABLES="${INST_ETC}/installer-variables"
INST_LOG="/var/log/apps/${TRIM_APPNAME}.log"
FWPORTS_FILE="/var/apps/${TRIM_APPNAME}/etc/${TRIM_APPNAME}.sc"
SHARE_PATH="/var/apps/${TRIM_APPNAME}/shares/${TRIM_APPNAME}"
LOG_FILE="${TRIM_PKGVAR}/${TRIM_APPNAME}.log"; PID_FILE="${TRIM_PKGVAR}/${TRIM_APPNAME}.pid"
SVC_WAIT_TIMEOUT=15; SVC_CWD="${TRIM_PKGVAR}"; SVC_BACKGROUND=y; SVC_WRITE_PID=y; SVC_QUIET=y
DOCKER_NAME=""; DNAME="${TRIM_APPNAME}"; SVC_NO_REDIRECT=""
OUT=/dev/null; [ -z "${SVC_NO_REDIRECT}" ] && OUT="${LOG_FILE}"
error_exit() { local msg="$1"; [ -n "${TRIM_TEMP_LOGFILE:-}" ] && echo "$msg" > "${TRIM_TEMP_LOGFILE}"; echo "ERROR: $msg" >&2; exit 1; }
validate_preinst()   { echo "validate_preinst"; }
service_preinst()    { echo "service_preinst"; }
service_postinst()   { echo "service_postinst"; }
service_preuninst()  { echo "service_preuninst"; }
service_postuninst() { echo "service_postuninst"; }
validate_preupgrade(){ echo "validate_preupgrade"; }
service_preupgrade() { echo "service_preupgrade"; }
service_save()       { echo "service_save"; }
service_restore()    { echo "service_restore"; }
service_postupgrade(){ echo "service_postupgrade"; }
service_preconfig()  { echo "service_preconfig"; }
service_postconfig() { echo "service_postconfig"; }
check_docker() { FILE_PATH="${TRIM_APPDEST}/app/docker-compose.yaml"; if [ -f "$FILE_PATH" ]; then DOCKER_NAME=$(cat $FILE_PATH | grep "container_name" | awk -F ':' '{print $2}' | xargs); fi; }; check_docker
install_log() { local _msg_="$@"; if [ -z "${_msg_}" ]; then while IFS=$'\n' read -r line; do install_log "${line}"; done; else echo -e "$(date +'%Y/%m/%d %H:%M:%S')\t${_msg_}" 1>&2; fi; }
call_func() { FUNC=$1; LOG=$2; if type "${FUNC}" 2>/dev/null | grep -q 'function' 2>/dev/null; then if [ -n "${LOG}" ]; then install_log "Begin ${FUNC}"; eval ${FUNC} 2>&1 | ${LOG}; install_log "End ${FUNC}"; else echo "Begin ${FUNC}" >> ${LOG_FILE}; eval ${FUNC} >> ${LOG_FILE}; echo "End ${FUNC}" >> ${LOG_FILE}; fi; fi; }
get_key_value() { cat $1 | grep -F "$2" | awk -F "=" '{print $2}' | tr -d ' ' | tr -d '"'; }
log_step() { install_log "===> Step $1. STATUS=${TRIM_APP_STATUS}"; }
save_wizard_variables() { [ -e "${INST_VARIABLES}" ] && $RM "${INST_VARIABLES}"; [ -n "${GROUP}" ] && echo "GROUP=${GROUP}" >> ${INST_VARIABLES}; [ -n "${SHARE_PATH}" ] && echo "SHARE_PATH=${SHARE_PATH}" >> ${INST_VARIABLES}; }
sync_var_folder() { if [ -d ${TRIM_APPDEST}/var -a "$(ls -A ${TRIM_APPDEST}/var 2>/dev/null)" ]; then $RSYNC --ignore-existing --remove-source-files ${TRIM_APPDEST}/var/ ${TRIM_PKGVAR}; find ${TRIM_APPDEST}/var -type f -exec sh -c 'x="{}"; mv "$x" "${x}.new"' \;; $RSYNC --remove-source-files ${TRIM_APPDEST}/var/ ${TRIM_PKGVAR}; $RM ${TRIM_APPDEST}/var; fi; }
install_init()      { log_step "install_init"; call_func "validate_preinst" install_log; call_func "service_preinst" install_log; exit 0; }
install_callback()  { log_step "install_callback"; call_func "save_wizard_variables" install_log; sync_var_folder; call_func "service_postinst" install_log; exit 0; }
uninstall_init()    { log_step "uninstall_init"; stop_daemon; call_func "service_preuninst" install_log; exit 0; }
uninstall_callback(){ log_step "uninstall_callback"; call_func "service_postuninst" install_log; if [ "$wizard_delete_data" = "true" ]; then echo "Removing files..." | install_log; [ "$(ls -A ${TRIM_PKGHOME} 2>/dev/null)" != "" ] && find ${TRIM_PKGHOME} -mindepth 1 -delete -print | install_log; [ "$(ls -A ${TRIM_PKGVAR} 2>/dev/null)" != "" ] && find ${TRIM_PKGVAR} -mindepth 1 -delete -print | install_log; fi; exit 0; }
upgrade_init()      { log_step "upgrade_init"; call_func "validate_preupgrade" install_log; stop_daemon; call_func "service_preupgrade" install_log; call_func "service_save" install_log; exit 0; }
fix_data_ownership() { if [ -n "${TRIM_USERNAME}" ] && [ -n "${TRIM_GROUPNAME}" ]; then local owner="${TRIM_USERNAME}:${TRIM_GROUPNAME}"; for dir in "${TRIM_PKGVAR}" "${TRIM_PKGETC}" "${TRIM_PKGHOME}"; do [ -d "$dir" ] && chown -R "$owner" "$dir" 2>/dev/null || true; done; fi; }
upgrade_callback()  { log_step "upgrade_callback"; call_func "fix_data_ownership" install_log; call_func "service_restore" install_log; call_func "service_postupgrade" install_log; exit 0; }
config_init()       { log_step "config_init"; call_func "service_preconfig" install_log; exit 0; }
config_callback()   { log_step "config_callback"; call_func "service_postconfig" install_log; exit 0; }
start_daemon() { if [ -n "$DOCKER_NAME" ]; then return; fi; i=0; [ -z "${SVC_QUIET}" ] && { [ -z "${SVC_KEEP_LOG}" ] && date > ${LOG_FILE} || date >> ${LOG_FILE}; }; call_func "service_prestart"; printf "%s" "$SERVICE_COMMAND" | while read -r service || [ -n "$service" ]; do i=$((i + 1)); [ -z "${SVC_QUIET}" ] && echo "Starting ${DNAME} command ${service}" >> ${LOG_FILE}; if [ -n "${service}" ]; then [ -n "${SVC_CWD}" ] && cd ${SVC_CWD}; if [ -z "${SVC_BACKGROUND}" ]; then ${service} >> ${OUT} 2>&1; else ${service} >> ${OUT} 2>&1 & fi; if [ -n "${SVC_WRITE_PID}" -a -n "${SVC_BACKGROUND}" -a -n "${PID_FILE}" ]; then [ $i -eq 1 ] && printf "%s" "$!" > ${PID_FILE} || printf "\n%s" "$!" >> ${PID_FILE}; else wait_for_status 0 ${SVC_WAIT_TIMEOUT:=20}; fi; fi; done; }
stop_daemon() { if [ -n "$DOCKER_NAME" ]; then return; fi; if [ -n "${PID_FILE}" -a -r "${PID_FILE}" ]; then for pid in $(cat "${PID_FILE}"); do [ -z "$pid" ] && continue; kill -TERM ${pid} >> ${LOG_FILE} 2>&1; wait_for_status 1 ${SVC_WAIT_TIMEOUT:=20} ${pid} || kill -KILL ${pid} >> ${LOG_FILE} 2>&1; done; [ -f "${PID_FILE}" ] && rm -f "${PID_FILE}"; fi; call_func "service_poststop"; }
daemon_status() { if [ -n "$DOCKER_NAME" ]; then docker inspect $DOCKER_NAME | grep -q '"Status": "running",' || exit 1; return; fi; status=0; [ -z "${1}" ] && pid_list=$(cat ${PID_FILE} 2>/dev/null) || pid_list=${1}; if [ -n "${pid_list}" ]; then for pid in ${pid_list}; do kill -0 ${pid} > /dev/null 2>&1; status=$((status + $?)); done; [ $status -ne 0 ] && { rm -f "${PID_FILE}"; return 1; } || return 0; else return 1; fi; }
wait_for_status() { counter=${2}; counter=${counter:=20}; while [ ${counter} -gt 0 ]; do daemon_status ${3}; [ $? -eq $1 ] && return; counter=$((counter - 1)); sleep 1; done; return 1; }
SHARED_COMMON

# cmd/main
cat > "$PKG_DIR/cmd/main" << 'EOF'
#!/bin/bash
COMMON=$(dirname $0)"/common"; [ -r "${COMMON}" ] && . "${COMMON}"
SVC_SETUP=$(dirname $0)"/service-setup"; [ -r "${SVC_SETUP}" ] && . "${SVC_SETUP}"
case $1 in
start) if daemon_status; then exit 0; else start_daemon; exit $?; fi ;;
stop) stop_daemon; exit 0 ;;
status) if daemon_status; then echo "${DNAME} is running"; exit 0; else echo "${DNAME} is not running"; exit 3; fi ;;
log) LINES="${2:-100}"; [ -f "${LOG_FILE}" ] && tail -n "$LINES" "${LOG_FILE}" || echo "No log"; exit 0 ;;
*) exit 1 ;;
esac
EOF

# cmd/installer
cat > "$PKG_DIR/cmd/installer" << 'EOF'
#!/bin/bash
COMMON=$(dirname $0)"/common"; [ -r "${COMMON}" ] && . "${COMMON}"
SVC_SETUP=$(dirname $0)"/service-setup"; [ -r "${SVC_SETUP}" ] && . "${SVC_SETUP}"
case "$1" in
install_init) install_init ;; install_callback) install_callback ;;
uninstall_init) uninstall_init ;; uninstall_callback) uninstall_callback ;;
upgrade_init) upgrade_init ;; upgrade_callback) upgrade_callback ;;
config_init) config_init ;; config_callback) config_callback ;;
*) exit 1 ;;
esac
EOF

# Hook scripts
for hook in install_init install_callback uninstall_init uninstall_callback upgrade_init upgrade_callback config_init config_callback; do
cat > "$PKG_DIR/cmd/$hook" << HOOK_EOF
#!/bin/bash
COMMON=\$(dirname \$0)"/common"; [ -r "\${COMMON}" ] && . "\${COMMON}"
SVC_SETUP=\$(dirname \$0)"/service-setup"; [ -r "\${SVC_SETUP}" ] && . "\${SVC_SETUP}"
${hook}
HOOK_EOF
done

# Overlay app-specific cmd/
cp "$FNOS_DIR"/cmd/* "$PKG_DIR/cmd/" 2>/dev/null || true
chmod +x "$PKG_DIR/cmd/"*

# Copy remaining files
cp -a "$FNOS_DIR/config" "$PKG_DIR/"
[ -d "$FNOS_DIR/wizard" ] && cp -a "$FNOS_DIR/wizard" "$PKG_DIR/" || true
cp "$FNOS_DIR"/*.sc "$PKG_DIR/" 2>/dev/null || true
cp "$FNOS_DIR"/ICON*.PNG "$PKG_DIR/" 2>/dev/null || true
cp -a "$FNOS_DIR/ui" "$PKG_DIR/"
[ -d "$PKG_DIR/ui/images" ] && [ -f "$PKG_DIR/ICON_256.PNG" ] && cp "$PKG_DIR/ICON_256.PNG" "$PKG_DIR/ui/images/256.png"
cp "$FNOS_DIR/manifest" "$PKG_DIR/manifest"
sed -i.tmp "s/^checksum.*/checksum        = ${CHECKSUM}/" "$PKG_DIR/manifest"
rm -f "$PKG_DIR/manifest.tmp" 2>/dev/null || true

# 4. Package
FPK_NAME="${APPNAME}_${VERSION}_${PLATFORM:-x86}.fpk"
info "打包 -> $FPK_NAME ..."
cd "$PKG_DIR" && tar -czf "$SCRIPT_DIR/$FPK_NAME" * && cd "$SCRIPT_DIR"
info "完成: $FPK_NAME ($(du -h "$FPK_NAME" | cut -f1))"
info "安装: 飞牛OS → 应用中心 → 手动安装 → 选择 $FPK_NAME"
