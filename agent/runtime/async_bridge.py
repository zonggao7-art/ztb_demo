import asyncio

from concurrent.futures import Executor, ThreadPoolExecutor

from typing import Any, Awaitable, Callable, Iterable, Optional

'''_IO_EXECUTOR'''
_IO_EXECUTOR: Optional[Executor] = None
_CPU_EXECUTOR: Optional[Executor] = None

def configure_executors(*, io_workers: int, cpu_workers: int) -> None:

    global _IO_EXECUTOR, _CPU_EXECUTOR

    #声明下面要修改的是模块级变量，不是函数内的局部变量

    _IO_EXECUTOR = ThreadPoolExecutor(max_workers=io_workers, #线程池最大线程数
                                      thread_name_prefix = "blocking-io",#给线程起名，方便日志/debug时区分)
    )
    _CPU_EXECUTOR = ThreadPoolExecutor(max_workers=cpu_workers,
                                       thread_name_prefix = "blocking-cpu")


async def run_blocking(
        function: Callable[..., Any], #要执行的同步函数
        /, #位置参数分隔符
        *args, #透传给function的位置参数
        executor: Optional[Executor] = None, #可选的Executor，默认IO池
        **kwargs, #透传给function的关键字参数
) -> Any:
    #获取"当前正在跑的事件循环" --这个函数必须在事件循环内被调用
    loop = asyncio.get_running_loop()
    #-get_running_loop(),没有运行中的loop时直接抛出RuntimeError
    #-get_event_loop(),会试图创建/复用，可能拿到错误的loop


    #把同步函数丢到线程池里跑，返回一个awaitable对象
    return await loop.run_in_executor(executor or _IO_EXECUTOR, #如果调用方没指定executor,就用全局的IO池
                                      lambda: function(*args, **kwargs),
                                      )
    '''整段等价于：1、把function(args, **kwargs)交给线程池里的某个工作线程
    2、当前协程挂起，让出事件循环3、工作线程跑完，把结果塞回future4、事件循环调度回来，
    await拿到结果，继续往下走'''


async def gather_limited(
        coros: Iterable[Awaitable[Any]], #一堆待执行的协程(list/tuple/generator)
        *,
        limit: int, #最大并发数
        return_exceptions: bool = True, #单个失败是否影响其他进程
) -> list[Any]:

    #新建一个信号量,许可证总数=limit
    semaphore = asyncio.Semaphore(limit)
    #注意: 每次调用gather_limited都会新建一个独立的semaphore,两次调用之间不会相互限流

    #内部包装函数: 把"先抢许可证、再await原协程"封装成一个新协程

    async def _wrap(coro: Awaitable[Any]) -> Any:
        async with semaphore:

            #信号量acquire: 抢许可证,如果当前许可证数>=1,则直接返回许可证,否则挂起,等待许可证
            #退出async with 时,会自动释放许可证
            return await coro
            #真正执行原协程，它可能立刻跑完，也可能挂起，等其他协程释放许可证

    
    #用asyncio.gather并发调度所有_wrap(c)
    return await asyncio.gather(
        *(_wrap(c) for c in coros),
        #生成器展开: 每个原协程都被_wrap包了一层，加上信号量控制
        #注意这里_wrap(c)返回的是协程对象,gather会自动调度他们
        return_exceptions = return_exceptions
    )

import atexit

def shutdown_executors() -> None:
    global _IO_EXECUTOR, _CPU_EXECUTOR
    for ex in (_IO_EXECUTOR, _CPU_EXECUTOR):
        if ex is not None:
            ex.shutdown(wait=True)
    _IO_EXECUTOR = _CPU_EXECUTOR = None