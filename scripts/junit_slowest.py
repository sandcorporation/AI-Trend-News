#!/usr/bin/env python3
"""junit XML 에서 느린 시험을 뽑는다 — 쓰임: junit_slowest.py <junit.xml> [상위 N=20] [임계 초=0.5]

`cpu_seconds`(시험 본문 CPU, conftest_cpu_budget.py 가 남긴다)와 `time`(setup·teardown 포함 벽시계)을 나란히 보인다.
time 만 크고 cpu_seconds 가 작으면 느린 것은 시험이 아니라 픽스처(setup)다.
"""

import sys
import xml.etree.ElementTree as ET


def main() -> int:
    path = sys.argv[1]
    top = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    threshold = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5
    rows = []
    for case in ET.parse(path).getroot().iter("testcase"):
        cpu = 0.0
        for prop in case.iter("property"):
            if prop.get("name") == "cpu_seconds":
                cpu = float(prop.get("value", "0"))
        rows.append((cpu, float(case.get("time", "0")), f"{case.get('classname')}::{case.get('name')}"))
    over = [r for r in rows if r[0] > threshold]
    print(f"시험 {len(rows)}건, cpu_seconds > {threshold}: {len(over)}건")
    print(f"{'cpu':>7} {'time':>7}  시험")
    for cpu, wall, name in sorted(rows, reverse=True)[:top]:
        print(f"{cpu:7.3f} {wall:7.3f}  {name}")
    print("-- time 이 크고 cpu 가 작은 것(픽스처가 느린 후보) --")
    for cpu, wall, name in sorted(rows, key=lambda r: r[1] - r[0], reverse=True)[:5]:
        print(f"{cpu:7.3f} {wall:7.3f}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
