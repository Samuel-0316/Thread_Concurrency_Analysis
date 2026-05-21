import threading

# Shared bank accounts
accounts = {
    'alice': 1000,
    'bob': 1000
}

def transfer(sender, receiver, amount):
    # Race condition: Multiple threads transferring money simultaneously
    # A thread might read the balance, get preempted, and write back stale data
    
    # Read balances
    sender_bal = accounts[sender]
    receiver_bal = accounts[receiver]
    
    if sender_bal >= amount:
        # Write balances
        accounts[sender] = sender_bal - amount
        accounts[receiver] = receiver_bal + amount

threads = []
# Alice transfers 10 to Bob 100 times concurrently
for _ in range(100):
    t = threading.Thread(target=transfer, args=('alice', 'bob', 10))
    threads.append(t)
    t.start()

for t in threads:
    t.join()

print(f"Final balances: {accounts}")
print(f"Total money (expected 2000): {sum(accounts.values())}")
