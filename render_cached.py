"""Render the last saved compatibility data during a static code deployment.

No live request or new verdict. Expired records retain their original timestamps;
clients already display them as unknown instead of extending a green state.
"""
import json
from pathlib import Path
import time
from collect import validate_public_document


def render(root):
    state=json.loads((root/'state.json').read_text())
    document=state['document'];history=state['history'];now=time.time()
    validate_public_document(document,now,allow_expired=True)
    if not isinstance(history,list) or len(history)>30:raise ValueError('INVALID_HISTORY')
    for item in history:validate_public_document(item,now,allow_expired=True)
    public=root/'public';public.mkdir(exist_ok=True)
    for name,value in [('status.json',document),('history.json',history)]:
        (public/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':
    render(Path(__file__).parent)
