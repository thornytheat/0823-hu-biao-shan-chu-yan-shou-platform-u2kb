EXPECTED = {'0001': (4, 2, 'READY'),
 '0002': (1, 1, 'READY'),
 '0003': (0, None, 'DATA_POSITION_CONFLICT'),
 '0004': (1, 1, 'DANGLING_POSITION')}
EVIDENCE = {'0001': {'visible': ['1:1', '3:0'],
          'removed': {'1:0': 'EQUALITY', '2:0': 'POSITION'},
          'dangling': []},
 '0004': {'visible': ['1:0'], 'removed': {}, 'dangling': ['1:12']}}
