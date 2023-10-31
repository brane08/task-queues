import calendar
import logging
import os
import random
import string
import threading
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, Future
from logging import Logger
from pathlib import Path
from queue import Queue
from typing import List

logger = logging.getLogger(__name__)


def worker_task(timeout: int, iterations: int):
    th = SampleTask(timeout, iterations)
    th.before()
    th.task()
    th.after()


def init_thread():
    threading.current_thread().name = get_next_thread_name()


def get_log_folder() -> Path:
    utc_now = time.gmtime()
    hour = int(int((utc_now.tm_hour / 8)) * 8)
    final_struct = (utc_now.tm_year, utc_now.tm_mon, utc_now.tm_mday, hour, 0, 0, 0, 0, 0)
    hour_folder = str(calendar.timegm(time.struct_time(final_struct)))
    return Path(Path(os.getcwd()).parent, "logs", hour_folder).resolve()


def get_next_thread_name(num: int = 16):
    return ''.join(random.SystemRandom().choice(string.ascii_lowercase + string.digits) for _ in range(num))


def init_thread_logger(file_name: str, logger: logging.Logger):
    formatter = logging.Formatter('%(threadName)s|%(asctime)s = %(message)s', datefmt='%Y:%m:%d-%H:%M:%S')
    file_handler = logging.FileHandler(Path(get_log_folder(), f"{file_name}.log"), mode='w')
    file_handler.setFormatter(formatter)
    logger.setLevel(logging.INFO)
    logger.addHandler(file_handler)
    logger.propagate = False


def destroy_thread_logger(logger: logging.Logger):
    logger.info("Clearing handlers!")
    while logger.hasHandlers():
        logger.handlers[0].flush()
        logger.removeHandler(logger.handlers[0])


class BaseTask(ABC):

    def __init__(self, timeout: int, iterations: int):
        self._timeout = timeout
        self._iterations = iterations

    @abstractmethod
    def before(self):
        raise NotImplementedError

    @abstractmethod
    def task(self):
        raise NotImplementedError

    @abstractmethod
    def after(self):
        raise NotImplementedError


class SampleTask(BaseTask):

    def __init__(self, timeout: int, iterations: int):
        super().__init__(timeout, iterations)
        self.thread_name = threading.current_thread().name
        self.logger: Logger = logging.getLogger(self.thread_name)

    def before(self):
        try:
            init_thread_logger(self.thread_name, self.logger)
            self.logger.info("This should be executed before start")
        except:
            logging.error("Error in start thread!")

    def task(self):
        try:
            self.logger.info("This is task execution")
            rand_int = random.randint(5, self._timeout)
            self.logger.info("Iterations: %d seconds: %d", self._iterations, rand_int)
            for iterate in range(0, self._iterations):
                self.logger.info("Iteration: %d should take seconds: %d", iterate, rand_int)
                time.sleep(rand_int)
            self.logger.info("This is task execution finished")
        except:
            logging.error("Error in performing task!")

    def after(self):
        try:
            self.logger.info("This should be executed after completed")
        except:
            logging.error("Error in finishing task!")
        finally:
            destroy_thread_logger(self.logger)
            del self.logger


class ThreadPool:
    __m_queue: Queue = None
    __executor: ThreadPoolExecutor = None
    __futures: List[Future] = list()

    @classmethod
    def setup_pool(cls, proc_num: int):
        cls.__m_queue = Queue(proc_num * 2)
        cls.__executor = ThreadPoolExecutor(proc_num, initializer=init_thread)
        logger.info("Thread pool is started:", str(proc_num))

    @classmethod
    def submit(cls, worker_num: int):
        for num in range(worker_num):
            iterations = random.randint(1, 5)
            f = cls.__executor.submit(worker_task, 15, iterations)
            cls.__futures.append(f)

    @classmethod
    def get_status(cls) -> dict:
        total = 0
        for fut in cls.__futures:
            if fut.running():
                total = total + 1
        return {"running": total}

    @classmethod
    def shutdown(cls):
        if cls.__futures is not None:
            for fut in cls.__futures:
                fut.cancel()
        if cls.__executor is not None:
            cls.__executor.shutdown(wait=False)
