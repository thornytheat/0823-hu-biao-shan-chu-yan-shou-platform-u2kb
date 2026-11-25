from app.business import decision


def policy(result):
    return decision(result,'DANGLING_POSITION' if result['flag'] else None)
