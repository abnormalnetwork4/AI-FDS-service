"""SQLite 변경 번호를 SSE로 전달합니다. 원문·사용자 정보는 스트림에 넣지 않습니다."""
import asyncio
from starlette.concurrency import run_in_threadpool


async def changes(repo, request, interval=0.25):
    # 공유 DB의 변경 번호를 사용하므로 여러 서버 프로세스에서도 갱신을 감지합니다.
    # 재접속은 전체 현재 목록을 다시 읽도록 알립니다. 이벤트 이력 재생 API는 아닙니다.
    previous = None
    idle = 0
    while not await request.is_disconnected():
        revision = await run_in_threadpool(repo.revision)
        if revision != previous:
            yield f"event: changed\nid: {revision}\nretry: 1000\ndata: {{\"revision\":{revision}}}\n\n"
            previous = revision
            idle = 0
        else:
            idle += interval
            if idle >= 15:
                yield ": keepalive\n\n"
                idle = 0
        await asyncio.sleep(interval)
