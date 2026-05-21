import threading
import time

class WebServer:
    def __init__(self):
        self.active_connections = 0
        self.total_requests = 0

    def handle_request(self):
        # Race condition: concurrent updates to multiple instance variables
        
        # Connection starts
        current_conns = self.active_connections
        time.sleep(0.001) # Simulate some delay/context switch
        self.active_connections = current_conns + 1
        
        # Do work...
        
        # Update metrics
        self.total_requests += 1
        
        # Connection ends
        current_conns = self.active_connections
        self.active_connections = current_conns - 1

server = WebServer()
threads = []

# Simulate 100 concurrent requests
for _ in range(100):
    t = threading.Thread(target=server.handle_request)
    threads.append(t)
    t.start()

for t in threads:
    t.join()

print(f"Active connections left (expected 0): {server.active_connections}")
print(f"Total requests processed: {server.total_requests}")
