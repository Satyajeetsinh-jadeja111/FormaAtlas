import json,sys
request=json.load(sys.stdin)
text=request.get('input','').strip()
print(json.dumps({'output':f'Your input contains {len(text.split())} words.\n\n{text}'}))
