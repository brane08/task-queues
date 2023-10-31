from time import sleep

from worker import wrkr


@wrkr.tasks
def sample():
    print("Will this work??? Should start sleep now")
    sleep(1 * 60)
    print("Sleep ended")
