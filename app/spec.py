COLUMNS = ('file', 'partition', 'sequence', 'position', 'row_key')
KINDS = ('data', 'position_delete', 'equality_delete', 'snapshot')
PROJECT = {'name': '湖表删除验收台', 'actor': '数据湖值班工程师', 'stages': [('intake', '样例采信'), ('prepare', '清单核验'), ('calculate', '删除应用'), ('policy', '快照比对'), ('balance', '运行对账')], 'chart': '快照生存表', 'mark': '湖'}
