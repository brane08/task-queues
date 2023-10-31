import datetime
import logging
import multiprocessing
import random
import time
import uuid
from concurrent.futures import Future, ProcessPoolExecutor
from logging import Logger
from multiprocessing import Manager
from multiprocessing.managers import SyncManager
from pathlib import Path
from queue import Queue, Empty
from typing import List

logger = logging.getLogger(__name__)


def worker_task(timeout: int, iterations: int, q: Queue):
    process = multiprocessing.current_process()
    q.put_nowait((process.name, process.pid))
    logger = multiprocessing.get_logger()
    init_process_logger(process.name, logger)
    rand_int = random.randint(1, timeout)
    logger.info("Process start: %s minutes: %d", process.name, rand_int)
    for iterate in range(0, iterations):
        logger.info("(%s) Iteration: %d should take minutes: %d", process.name, iterate, rand_int)
        time.sleep(datetime.timedelta(minutes=rand_int).seconds)
    logger.info("Process stop: %s", process)


def init_process_logger(file_name: str, logger: Logger):
    formatter = logging.Formatter('%(asctime)s: %(message)s', datefmt='%Y/%m/%d %H:%M:%S')
    file_handler = logging.FileHandler(Path(ProcessPool.get_log_folder(), f"{file_name}.log"), mode='w')
    file_handler.setFormatter(formatter)
    logger.setLevel(logging.INFO)
    logger.addHandler(file_handler)


def init_process():
    name = uuid.uuid4().hex
    multiprocessing.current_process().name = name


class ProcFuture:

    def __init__(self, name: str, pid: int, fut: Future):
        self.__name = name
        self.__pid = pid
        self.__future = fut

    @property
    def name(self):
        return self.__name

    @property
    def pid(self):
        return self.__pid

    @property
    def future(self):
        return self.__future


class ProcessPool:
    __manager: SyncManager = None
    __m_queue: Queue = None
    __gl_dict: dict = None
    __executor: ProcessPoolExecutor = None
    __futures: List[ProcFuture] = list()

    @classmethod
    def setup_pool(cls, proc_num: int):
        cls.__manager = Manager()
        cls.__gl_dict = cls.__manager.dict()
        cls.__m_queue = cls.__manager.Queue(proc_num * 2)
        cls.__executor = ProcessPoolExecutor(proc_num, initializer=init_process)

    @classmethod
    def submit(cls, worker_num: int):
        for num in range(worker_num):
            iterations = random.randint(5, 10)
            f = cls.__executor.submit(worker_task, 4, iterations, cls.__m_queue)
            try:
                name, pid = cls.__m_queue.get(block=True, timeout=1000)
                cls.__futures.append(ProcFuture(name, pid, f))
            except Empty:
                logger.info("This should not occur")

    @classmethod
    def get_status(cls) -> dict:
        return {fut.pid: fut.future.running() for fut in cls.__futures}

    @classmethod
    def shutdown(cls):
        if cls.__manager is not None:
            cls.__manager.shutdown()
        if cls.__futures is not None:
            for fut in cls.__futures:
                fut.future.cancel()
        if cls.__executor is not None:
            cls.__executor.shutdown(wait=False)
