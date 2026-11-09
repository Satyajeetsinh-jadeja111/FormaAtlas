"""Independent incident dispatcher and liveness monitor; no agent work executed."""
import signal,time
import server,observability

def main():
 server.init();stop=False
 def shutdown(*args):
  nonlocal stop
  stop=True
 signal.signal(signal.SIGTERM,shutdown);signal.signal(signal.SIGINT,shutdown)
 try:
  while not stop:
   try:
    observability.maintenance(server.db)
    for _ in range(20):
     if not observability.dispatch(server.db):break
   except Exception:
    # Do not recursively report mail/logging system failures.
    pass
   for _ in range(20):
    if stop:break
    time.sleep(.25)
 finally:pass

if __name__=='__main__':main()
