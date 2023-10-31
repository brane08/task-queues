from celery import Celery

# mongodb+srv://mongofx_user:UFNTn0T9NuL0EGUc@mongofxcluster1.2yunc.mongodb.net/celery?retryWrites=true&w=majority
# mongodb://mongofx_user:UFNTn0T9NuL0EGUc@mongofxcluster1-shard-00-00.2yunc.mongodb.net:27017,mongofxcluster1-shard-00-01.2yunc.mongodb.net:27017,mongofxcluster1-shard-00-02.2yunc.mongodb.net:27017/?ssl=true&replicaSet=atlas-123nzn-shard-0&authSource=admin&retryWrites=true&w=majority
# mongofx_user OzcaDA5mFAGXLEMm

mongo_url = """
mongodb://mongofx_user:UFNTn0T9NuL0EGUc@mongofxcluster1-shard-00-00.2yunc.mongodb.net:27017,mongofxcluster1-shard-00-01.2yunc.mongodb.net:27017,mongofxcluster1-shard-00-02.2yunc.mongodb.net:27017/celery?ssl=true&replicaSet=atlas-123nzn-shard-0&authSource=admin&retryWrites=true&w=majority
"""

app = Celery(
    'tasks',
    broker=mongo_url,
    # ## add result backend here if needed.
    backend='mongodb',
    task_acks_late=True
)

if __name__ == "__main__":
    works = app.Worker(include=["tasks"])
    works.start()
