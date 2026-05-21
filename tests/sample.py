import threading
balance = 0
lock = threading.Lock()

def worker():
    global balance
    for _ in range(10):
        with lock:
            balance += 1

threads = []
for i in range(2):
    t = threading.Thread(target=worker)
    threads.append(t)
    t.start()
for t in threads:
    t.join()
