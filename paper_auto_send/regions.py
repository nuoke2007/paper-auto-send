from __future__ import annotations

import re

# Shared district names (e.g. 鼓楼区) alone cannot establish the province.
JIANGSU_PLACES = (
    "南京 苏州 无锡 常州 镇江 扬州 泰州 南通 盐城 淮安 宿迁 徐州 连云港".split()
    + "江阴市 宜兴市 溧阳市 昆山市 常熟市 张家港市 太仓市 启东市 如皋市 海安市 "
    "东台市 仪征市 高邮市 丹阳市 扬中市 句容市 兴化市 靖江市 泰兴市 新沂市 邳州市 "
    "灌云县 灌南县 东海县 沭阳县 泗阳县 泗洪县 阜宁县 射阳县 建湖县 滨海县 响水县 "
    "宝应县 如东县 丰县 沛县 睢宁县 盱眙县 金湖县 涟水县".split()
    + "玄武区 秦淮区 建邺区 栖霞区 雨花台区 江宁区 浦口区 六合区 溧水区 高淳区 "
    "梁溪区 滨湖区 惠山区 锡山区 新吴区 天宁区 钟楼区 新北区 武进区 金坛区 "
    "虎丘区 姑苏区 吴中区 相城区 吴江区 崇川区 海门区 亭湖区 盐都区 大丰区 "
    "淮阴区 洪泽区 清江浦区 广陵区 邗江区 江都区 京口区 润州区 丹徒区 "
    "海陵区 高港区 姜堰区 宿城区 宿豫区 云龙区 贾汪区 泉山区 铜山区 "
    "连云区 赣榆区 苏州工业园区".split()
)


def is_jiangsu_title(title: str) -> bool:
    compact = re.sub(r"\s+", "", title)
    if "江苏" in compact:
        return True
    for place in JIANGSU_PLACES:
        if place in compact:
            if place.endswith(("市", "县", "区")) or re.search(re.escape(place) + r"(?:市|地区)", compact):
                return True
    return False
