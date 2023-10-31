import rq
from redis import Redis
from time import sleep


def main():
    print("I'm started!!!")
    redis = Redis.from_url('redis://')
    queue = rq.Queue('pagila-tasks', connection=redis)
    for i in range(1, 20):
        job = queue.enqueue('tasks.samples.sample')
        print(f"ID: {str(job.get_id())} and sleep for 5 seconds")
        sleep(5)


if __name__ == "__main__":
    main()
