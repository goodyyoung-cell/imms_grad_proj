"""배포된 Cloud Run 설정으로 로컬 .env를 만든다 (키를 메신저로 주고받지 않기 위해).

Cloud Run 서비스(sdr-agent)에 연결된 설정을 읽어, 비밀값은 Secret Manager에서 가져오고
일반 설정은 그대로 옮긴다. 나머지 칸은 .env.example의 기본값을 쓴다.

필요: gcloud 로그인 계정이 GCP 프로젝트의 'Secret Manager 보안 비밀 접근자' 권한을 가질 것.

  gcloud auth login
  gcloud config set project gen-lang-client-0136144273
  python scripts/pull_env.py            # .env가 이미 있으면 멈춘다
  python scripts/pull_env.py --force    # 기존 .env를 .env.bak으로 옮기고 새로 만든다

키 값은 화면에 출력하지 않는다. 만든 .env는 커밋하지 않는다 (.gitignore).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICE, REGION = "sdr-agent", "asia-northeast3"


def gcloud(*args: str) -> str:
    try:
        return subprocess.run(["gcloud", *args], check=True, capture_output=True, text=True).stdout
    except FileNotFoundError:
        sys.exit("gcloud가 없습니다. https://cloud.google.com/sdk/docs/install 에서 설치하세요.")
    except subprocess.CalledProcessError as exc:
        sys.exit(f"gcloud 실패: gcloud {' '.join(args[:3])} ...\n{exc.stderr.strip()[:400]}")


def deployed_values() -> tuple[dict[str, str], list[str]]:
    svc = json.loads(gcloud("run", "services", "describe", SERVICE, "--region", REGION, "--format=json"))
    env = svc["spec"]["template"]["spec"]["containers"][0].get("env", [])
    values, secrets = {}, []
    for e in env:
        ref = (e.get("valueFrom") or {}).get("secretKeyRef")
        if ref:
            values[e["name"]] = gcloud("secrets", "versions", "access", ref.get("key") or "latest",
                                       f"--secret={ref['name']}").rstrip("\n")
            secrets.append(e["name"])
        else:
            values[e["name"]] = e.get("value", "")
    return values, secrets


def render(template: str, values: dict[str, str]) -> str:
    out, used = [], set()
    for line in template.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
        if m and m.group(1) in values:
            out.append(f"{m.group(1)}={values[m.group(1)]}")
            used.add(m.group(1))
        else:
            out.append(line)
    extra = [k for k in values if k not in used]
    if extra:
        out += ["", "# Cloud Run에만 있던 설정"] + [f"{k}={values[k]}" for k in extra]
    return "\n".join(out) + "\n"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true", help="기존 .env를 .env.bak으로 옮기고 새로 만든다")
    args = p.parse_args()
    env_path = ROOT / ".env"
    if env_path.exists() and not args.force:
        sys.exit(".env가 이미 있습니다. 덮어쓰려면 --force (기존 파일은 .env.bak으로 보관)")
    values, secrets = deployed_values()
    text = render((ROOT / ".env.example").read_text(encoding="utf-8"), values)
    if env_path.exists():
        shutil.copy2(env_path, ROOT / ".env.bak")
    env_path.write_text(text, encoding="utf-8")
    env_path.chmod(0o600)
    print(f".env 생성: 비밀값 {len(secrets)}개 ({', '.join(sorted(secrets))}), 일반 설정 {len(values) - len(secrets)}개")


if __name__ == "__main__":
    main()
