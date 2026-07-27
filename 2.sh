#!/usr/bin/env bash
# 오류 발생 시 즉시 중단 및 미정의 변수 사용 방지
set -euo pipefail

# 1. 작업 대상 프로젝트 디렉토리로 이동
cd /Users/lee/Movies/artwork/apps/AppForge-LLM

# 2. Git 참조 영역의 불필요한 .DS_Store 파일 백업/이동
if test -f .git/refs/.DS_Store; then
  mv .git/refs/.DS_Store ../AppForge-LLM-invalid-ref.DS_Store
fi

# 3. 임시 스크립트 파일(1.sh) 백업/이동
if test -f 1.sh; then
  mv 1.sh ../AppForge-LLM-filter-repo-1.sh
fi

# 4. Git 무결성 검사, 상태 및 용량 확인
git fsck --full
git status --short
du -sh .git