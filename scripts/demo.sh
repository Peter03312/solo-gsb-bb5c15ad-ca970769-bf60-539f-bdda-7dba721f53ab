#!/bin/sh
# 端到端调用示例（不依赖 jq）。
# 用法：
#   sh scripts/demo.sh
# 建议在全新栈上运行： docker compose down -v && docker compose up -d --build
set -eu

BASE="${BASE_URL:-http://localhost:8000}"
T1="2026-10-05T10:00:00Z"
T2="2026-10-05T12:00:00Z"
T3="2026-10-05T11:00:00Z"
T4="2026-10-05T13:00:00Z"

hr() { printf '\n========== %s ==========\n' "$1"; }
req() {
    method="$1"; path="$2"; body="${3:-}"
    if [ -n "$body" ]; then
        curl -sS -X "$method" "$BASE$path" \
            -H 'Content-Type: application/json' -d "$body"
    else
        curl -sS -X "$method" "$BASE$path"
    fi
    printf '\n'
}

hr "0. 健康检查"
req GET /health

hr "1. 非法申请整笔拒收（radius <= 0）-> 422"
req POST /api/applications '{"center_x":0,"center_y":0,"radius":0,"freq_low_mhz":450,"freq_high_mhz":452,"start_utc":"'"$T1"'","end_utc":"'"$T2"'","applicant":"bad"}'

hr "2. 非法申请整笔拒收（freq_low > freq_high）-> 422"
req POST /api/applications '{"center_x":0,"center_y":0,"radius":1000,"freq_low_mhz":460,"freq_high_mhz":450,"start_utc":"'"$T1"'","end_utc":"'"$T2"'"}'

hr "3. 非法申请整笔拒收（start >= end）-> 422"
req POST /api/applications '{"center_x":0,"center_y":0,"radius":1000,"freq_low_mhz":450,"freq_high_mhz":452,"start_utc":"'"$T2"'","end_utc":"'"$T1"'"}'

hr "4. 创建申请 A：圆心(0,0) 半径1000，450.00-452.00 MHz，[10:00,12:00)"
req POST /api/applications '{"center_x":0,"center_y":0,"radius":1000,"freq_low_mhz":450.00,"freq_high_mhz":452.00,"start_utc":"'"$T1"'","end_utc":"'"$T2"'","applicant":"Alpha","purpose":"concert main"}'

hr "5. 预览 A：无冲突，current_revision=0"
req GET /api/applications/1/preview

hr "6. 携带修订号 0 批准 A -> revision 1"
req POST /api/applications/1/approve '{"expected_revision":0}'

hr "7. 创建申请 B：圆与时段均与 A 相交，频段 451.00-453.00 与 A 相交"
req POST /api/applications '{"center_x":500,"center_y":0,"radius":600,"freq_low_mhz":451.00,"freq_high_mhz":453.00,"start_utc":"'"$T3"'","end_utc":"'"$T4"'","applicant":"Bravo","purpose":"stage relay"}'

hr "8. 预览 B：列出冲突对象 A、触发条件 band_overlap、current_revision=1"
req GET /api/applications/2/preview

hr "9. 用过期修订号 0 批准 B -> 409 STALE_REVISION（不得占用频段）"
req POST /api/applications/2/approve '{"expected_revision":0}'

hr "10. 用当前修订号 1 批准 B -> 409 CONFLICT（与已批准申请相撞）"
req POST /api/applications/2/approve '{"expected_revision":1}'

hr "11. 创建申请 C：频段 452.20-453.00，与 A 间隔 0.20 < 保护带宽 0.25 -> guard_band 冲突"
req POST /api/applications '{"center_x":0,"center_y":0,"radius":1000,"freq_low_mhz":452.20,"freq_high_mhz":453.00,"start_utc":"'"$T1"'","end_utc":"'"$T2"'","applicant":"Charlie"}'

hr "12. 预览 C：trigger=guard_band, band_gap_mhz=0.2, required=0.25"
req GET /api/applications/3/preview

hr "13. 创建申请 D：空间远离 A/B/C，即使频段相近也不构成干扰"
req POST /api/applications '{"center_x":5000,"center_y":0,"radius":100,"freq_low_mhz":451.50,"freq_high_mhz":452.50,"start_utc":"'"$T3"'","end_utc":"'"$T4"'","applicant":"Delta"}'

hr "14. 预览并批准 D -> revision 2"
req GET /api/applications/4/preview
req POST /api/applications/4/approve '{"expected_revision":1}'

hr "15. 撤销 A（只影响后续裁决的有效集合）-> revision 3"
req POST /api/applications/1/revoke

hr "16. B 重新预览：有效集合只剩 D，空间不相交 -> 无冲突，current_revision=3"
req GET /api/applications/2/preview

hr "17. 携带修订号 3 批准 B -> revision 4"
req POST /api/applications/2/approve '{"expected_revision":3}'

hr "18. 查询修订号 1：还原当时状态（A 有效，尚无撤销记录）"
req GET /api/revisions/1

hr "19. 查询修订号 3：A 已撤销，当时有效集合为 D，事件含批准/撤销记录"
req GET /api/revisions/3

hr "20. 当前修订号与完整修订流"
req GET /api/revisions

hr "21. 超出范围的修订号 -> 404"
req GET /api/revisions/999

echo
echo "演示完成。交互式文档： $BASE/docs"
