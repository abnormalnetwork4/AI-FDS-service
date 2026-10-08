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

echo "== 3/6 .env (Claude/MonoGPT 키)"
if [ ! -f "$APP_DIR/backend/.env" ]; then
  sudo -u "$APP_USER" cp "$APP_DIR/backend/.env.example" "$APP_DIR/backend/.env"
  echo "   backend/.env를 만들었습니다. 설치 후 키를 넣고 'sudo systemctl restart fds-chat' 하세요."
fi
chmod 600 "$APP_DIR/backend/.env"

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
if [ ! -f /etc/nginx/.htpasswd ]; then
  read -rp "   화면 접속 아이디(예: team): " WEB_USER
  htpasswd -c /etc/nginx/.htpasswd "$WEB_USER"
fi
nginx -t && systemctl reload nginx

IP="$(curl -s --max-time 3 http://checkip.amazonaws.com || echo '<EC2 공인 IP>')"
echo
echo "== 완료"
echo "   대시보드: http://$IP/"
echo "   사내 AI 채팅: http://$IP/chat.html"
echo "   상태 확인: sudo systemctl status fds fds-chat --no-pager"
