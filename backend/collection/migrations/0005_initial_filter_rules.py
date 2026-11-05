from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

ZDKOREA_INCLUDE = (
    "AI, 인공지능, LLM, GPT, 에이전트, 반도체, 칩, GPU, HBM, D램, 파운드리, 클라우드, 데이터센터, "
    "보안, 해킹, 랜섬, 개인정보, 소프트웨어, SW, 오픈소스, 개발자, 코딩, 플랫폼, 로봇, 휴머노이드, "
    "자율주행, 양자, 통신, 5G, 6G, 네트워크, 서버, 스마트폰, 애플, 구글, MS, 마이크로소프트, 메타, "
    "오픈AI, 앤트로픽, 엔비디아, 삼성, SK, LG, 네이버, 카카오, 테슬라, 아마존, 인텔, AMD, 퀄컴, "
    "TSMC, IT, 디지털, 전산, 망, 사이버, 빅테크, 스타트업, 투자 유치, 컴퓨팅, 모델"
)

INITIAL: dict[str, dict[str, list[str]]] = {
    "https://feeds.feedburner.com/zdkorea": {"include_any": ZDKOREA_INCLUDE.split(", ")},
    "https://feeds.arstechnica.com/arstechnica/index": {
        "exclude_url": ["/health/", "/cars/", "/space/", "/science/"]
    },
    "https://techcrunch.com/feed/": {
        "exclude_url": ["/video/", "/podcast/"],
        "exclude_title": ["TechCrunch Disrupt", "Founder Summit", "StrictlyVC"],
    },
}


def forwards(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    source = apps.get_model("collection", "Source")
    for url, rules in INITIAL.items():
        for s in source.objects.filter(url=url):
            if not s.filter_rules:  # 이미 규칙이 있으면 건드리지 않는다
                s.filter_rules = rules
                s.save(update_fields=["filter_rules"])


def backwards(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    source = apps.get_model("collection", "Source")
    source.objects.filter(url__in=list(INITIAL)).update(filter_rules={})


class Migration(migrations.Migration):
    dependencies = [("collection", "0004_filter_rules")]
    operations = [migrations.RunPython(forwards, backwards)]
