#!/usr/bin/env bash
# AI-FDS-service 서버 설치 스크립트 (Ubuntu 24.04, EC2).
# 실행: 저장소 폴더에서  sudo bash deploy/install.sh
# 다시 실행해도 됩니다(코드 업데이트 후 재설치·재시작용).
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="${SUDO_USER:-ubuntu}"
echo "== 설치 위치: $APP_DIR (실행 계정: $APP_USER)"

echo "== 1/6 시스템 패키지"
apt-get update -y
apt-get install -y python3-venv python3-pip nginx apache2-utils git curl
if ! command -v node >/dev/null || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 22 ]; then
  # 대시보드 빌드 도구(Vite 8)는 Node 22 이상이 필요합니다.
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y nodejs
fi

echo "== 2/6 백엔드 파이썬 환경"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/backend/.venv"
sudo -u "$APP_USER" "$APP_DIR/backend/.venv/bin/pip" install -q --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/backend/.venv/bin/pip" install -q -r "$APP_DIR/backend/requirements.txt"
mkdir -p "$APP_DIR/backend/data" && chown "$APP_USER" "$APP_DIR/backend/data"

echo "== 3/6 .env (AI 키·설정)"
ENV="$APP_DIR/backend/.env"
[ -f "$ENV" ] || sudo -u "$APP_USER" cp "$APP_DIR/backend/.env.example" "$ENV"
sed -i 's/\r$//' "$ENV"  # 윈도우에서 만든 줄바꿈 정리
env_get() { grep -E "^$1=" "$ENV" | tail -1 | cut -d= -f2- | tr -d '"'"'"' '; }
env_set() {  # 같은 키는 지우고 마지막에 한 줄로 씁니다(값이 둘로 갈리는 문제 방지)
  sed -i "/^$1=/d" "$ENV"; echo "$1=$2" >> "$ENV"
}
KEY="$(env_get ANTHROPIC_API_KEY)"
if [ -z "$KEY" ] && [ -t 0 ]; then
  read -rsp "   MonoGPT(또는 Anthropic) API 키를 붙여 넣으세요(화면에 안 보임, 건너뛰려면 Enter): " KEY; echo
  [ -n "$KEY" ] && env_set ANTHROPIC_API_KEY "$KEY"
fi
# 주소가 없으면: Anthropic 공식 키(sk-ant-)가 아니면 MonoGPT 주소·모델을 넣습니다.
if [ -z "$(env_get ANTHROPIC_BASE_URL)" ] && [ "${KEY#sk-ant-}" = "$KEY" ]; then
  env_set ANTHROPIC_BASE_URL "https://monogpt.kr/api/monorouter/v1/anthropic/v1"
  env_set CLAUDE_MODEL "claude-sonnet-4-6"
  echo "   ANTHROPIC_BASE_URL을 MonoGPT 주소로 설정했습니다."
fi
[ -n "$(env_get FDS_ALLOW_CLEAR)" ] || env_set FDS_ALLOW_CLEAR 1
chown "$APP_USER" "$ENV"; chmod 600 "$ENV"
echo "   기록 비우기: $( [ "$(env_get FDS_ALLOW_CLEAR)" = 0 ] && echo 꺼짐 || echo 켜짐 ) (backend/.env의 FDS_ALLOW_CLEAR)"

echo "== 4/6 대시보드·채팅 화면 빌드"
sudo -u "$APP_USER" bash -c "cd '$APP_DIR/frontend' && npm ci --silent && npm run build --silent"
# nginx가 홈 폴더 안 파일을 읽을 수 있게 경로의 실행 권한을 엽니다(파일 내용 쓰기 권한은 주지 않음).
d="$APP_DIR/frontend/dist"; while [ "$d" != "/" ]; do chmod o+x "$d"; d="$(dirname "$d")"; done
chmod -R o+r "$APP_DIR/frontend/dist"

echo "== 5/6 서버 자동 실행(systemd)"
for svc in fds fds-chat; do
  sed -e "s#__APP_DIR__#$APP_DIR#g" -e "s#__APP_USER__#$APP_USER#g" "$APP_DIR/deploy/$svc.service" > "/etc/systemd/system/$svc.service"
done
systemctl daemon-reload
systemctl enable --now fds fds-chat
systemctl restart fds fds-chat

echo "== 6/6 웹 서버(nginx) + 접속 비밀번호"
sed -e "s#__APP_DIR__#$APP_DIR#g" "$APP_DIR/deploy/nginx.conf" > /etc/nginx/sites-available/ai-fds
ln -sf /etc/nginx/sites-available/ai-fds /etc/nginx/sites-enabled/ai-fds
rm -f /etc/nginx/sites-enabled/default
if [ ! -s /etc/nginx/.htpasswd ]; then
  read -rp "   화면 접속 아이디(예: team): " WEB_USER
  # 비밀번호 두 번이 다르면 다시 묻습니다.
  until htpasswd -c /etc/nginx/.htpasswd "$WEB_USER"; do echo "   비밀번호가 서로 달랐습니다. 다시 입력하세요."; done
fi
nginx -t && systemctl reload nginx

echo "== 확인"
for _ in $(seq 1 20); do curl -sf http://127.0.0.1:8000/health >/dev/null && break; sleep 1; done
curl -sf http://127.0.0.1:8000/health >/dev/null && echo "   FDS 서버: 정상" || echo "   FDS 서버: 응답 없음 → journalctl -u fds -n 50"
for _ in $(seq 1 10); do curl -sf http://127.0.0.1:8100/chat-api/health >/dev/null && break; sleep 1; done
CHAT="$(curl -sf http://127.0.0.1:8100/chat-api/health || true)"
if [ -z "$CHAT" ]; then echo "   채팅 서버: 응답 없음 → journalctl -u fds-chat -n 50"
else echo "   채팅 서버: $(echo "$CHAT" | python3 -c 'import sys,json;d=json.load(sys.stdin);print("AI 연결 설정됨" if d["ai_configured"] else "AI 키 없음", "·", d["ai_base_url"], "·", d["model"], ("· 경고: "+d["warning"]) if d.get("warning") else "")')"
fi

IP="$(curl -s --max-time 3 http://checkip.amazonaws.com || echo '<EC2 공인 IP>')"
echo
echo "== 완료"
echo "   대시보드: http://$IP/"
echo "   사내 AI 채팅: http://$IP/chat.html"
echo "   상태 확인: sudo systemctl status fds fds-chat --no-pager"
