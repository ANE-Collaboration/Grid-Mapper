"""Official public download catalog. Discover current file names every run."""

SOURCES = {
    "hokkaido": {
        "name": "北海道電力ネットワーク", "url": "https://www.hepco.co.jp/network/con_service/public_document/bid_info.html",
        "pattern": r"/sys_capa_(?:kikan|local\d+)\.zip$", "minimum_files": 25,
    },
    "tohoku": {
        "name": "東北電力ネットワーク", "url": "https://nw.tohoku-epco.co.jp/consignment/system/announcement/index.html",
        "pattern": r"/sys_capa_\w+_line_\d+_\d+\.csv$", "minimum_files": 8,
    },
    "tepco": {
        "name": "東京電力パワーグリッド", "url": "https://www.tepco.co.jp/pg/consignment/system/index-j.html",
        "pattern": r"/csv_yosochoryu_\w+\.zip$", "minimum_files": 14,
    },
    "chubu": {
        "name": "中部電力パワーグリッド", "url": "https://gridmap.powergrid.chuden.co.jp/",
        "pattern": None, "minimum_files": 7,
    },
    "hokuriku": {
        "name": "北陸電力送配電", "url": "https://www.rikuden.co.jp/nw_notification/U_154seiyaku.html",
        "pattern": r"/sys_capa_\w+_line_\d+_\d+\.csv$", "minimum_files": 4,
    },
    "kansai": {
        "name": "関西電力送配電", "url": "https://www.kansai-td.co.jp/consignment/disclosure/distribution-equipment/index.html",
        "pattern": r"/154kv_(?:more|less)_line\.csv$", "minimum_files": 2,
    },
    "chugoku": {
        "name": "中国電力ネットワーク", "url": "https://www.energia.co.jp/nw/service/retailer/keitou/access/",
        "pattern": r"/csv_(?:220kv|tori|shima|oka|hiro|yama)\.zip$", "minimum_files": 6,
    },
    "shikoku": {
        "name": "四国電力送配電", "url": "https://www.yonden.co.jp/nw/line_access/data.html",
        "pattern": r"/sys_capa_\w+_line_\d+_\d+\.csv$", "minimum_files": 5,
    },
    "kyushu": {
        "name": "九州電力送配電", "url": "https://www.kyuden.co.jp/td/service/wheeling/disclosure.html",
        "pattern": r"\.zip$", "text_pattern": "送電線CSV", "minimum_files": 1,
    },
    "okinawa": {
        "name": "沖縄電力", "url": "https://www.okiden.co.jp/business-support/service/rule/plan/index.html",
        "pattern": r"/con_res_map\d+_01\.csv$", "minimum_files": 3,
    },
}
