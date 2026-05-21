import threading

counter = 0

def increment_counter():
    global counter
    for _ in range(100000):
        counter += 1

threads = []
for i in range(5):
    t = threading.Thread(target=increment_counter)
    threads.append(t)
    t.start()

for t in threads:
    t.join()

print(f"Expected 500000, got: {counter}")