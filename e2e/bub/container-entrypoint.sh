#!/bin/sh
set -eu

docker_log=/tmp/powercontext-e2e-dockerd.log
storage_driver=${POWERCONTEXT_E2E_DOCKER_STORAGE_DRIVER:-vfs}
server_address=${POWERCONTEXT_E2E_SERVER_ADDRESS:-powercontext:8000}

dockerd \
    --host=unix:///var/run/docker.sock \
    --storage-driver="$storage_driver" \
    --log-level=error \
    >"$docker_log" 2>&1 &

attempt=0
until docker info >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 60 ]; then
        echo "The nested Docker daemon did not become ready." >&2
        sed -n '1,160p' "$docker_log" >&2
        exit 1
    fi
    sleep 1
done

socat TCP-LISTEN:8000,bind=0.0.0.0,fork,reuseaddr "TCP:$server_address" &

exec powercontext-e2e "$@"
