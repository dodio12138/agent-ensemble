/*
 * Absolute, low-jitter waits for the band scheduler thread.
 *
 * The clock matches Python's time.perf_counter_ns(): mach_absolute_time on macOS and
 * CLOCK_MONOTONIC on Linux, so deadlines computed in Python can be passed through
 * unchanged. A wait sleeps until shortly before the deadline with an absolute kernel
 * timer, then spins for the final stretch. ctypes releases the GIL around each call.
 */
#include <errno.h>
#include <pthread.h>
#include <stdint.h>
#include <time.h>

#if defined(__APPLE__)
#include <mach/mach.h>
#include <mach/mach_time.h>
#include <mach/thread_policy.h>
#else
#include <sched.h>
#endif

#if defined(__x86_64__) || defined(__i386__)
#define CPU_RELAX() __asm__ __volatile__("pause")
#elif defined(__aarch64__)
#define CPU_RELAX() __asm__ __volatile__("yield")
#else
#define CPU_RELAX() ((void)0)
#endif

#if defined(__APPLE__)
static mach_timebase_info_data_t timebase;

static void ensure_timebase(void) {
    if (timebase.denom == 0) {
        mach_timebase_info(&timebase);
    }
}

static uint64_t ns_to_ticks(int64_t ns) {
    ensure_timebase();
    return (uint64_t)((__uint128_t)ns * timebase.denom / timebase.numer);
}
#endif

int64_t ae_now_ns(void) {
#if defined(__APPLE__)
    ensure_timebase();
    return (int64_t)((__uint128_t)mach_absolute_time() * timebase.numer / timebase.denom);
#else
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (int64_t)now.tv_sec * 1000000000LL + now.tv_nsec;
#endif
}

/* Returns how late the call returned, in nanoseconds (0 when on time). */
int64_t ae_wait_until(int64_t deadline_ns, int64_t spin_ns) {
    int64_t wake = deadline_ns - (spin_ns > 0 ? spin_ns : 0);
    if (wake > ae_now_ns()) {
#if defined(__APPLE__)
        mach_wait_until(ns_to_ticks(wake));
#else
        struct timespec target = {
            .tv_sec = (time_t)(wake / 1000000000LL),
            .tv_nsec = (long)(wake % 1000000000LL),
        };
        while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &target, NULL) == EINTR) {
        }
#endif
    }
    int64_t now = ae_now_ns();
    while (now < deadline_ns) {
        CPU_RELAX();
        now = ae_now_ns();
    }
    return now - deadline_ns;
}

/*
 * Ask the kernel to treat the calling thread as time-critical. Returns 1 on success.
 * macOS: a time-constraint policy, as used by audio threads; the kernel demotes the
 * thread on its own if it overruns. Linux: SCHED_FIFO, which usually needs privileges.
 */
int ae_promote_thread(int64_t computation_ns, int64_t constraint_ns) {
#if defined(__APPLE__)
    thread_time_constraint_policy_data_t policy;
    policy.period = 0;
    policy.computation = (uint32_t)ns_to_ticks(computation_ns);
    policy.constraint = (uint32_t)ns_to_ticks(constraint_ns);
    policy.preemptible = 1;
    kern_return_t result = thread_policy_set(
        pthread_mach_thread_np(pthread_self()),
        THREAD_TIME_CONSTRAINT_POLICY,
        (thread_policy_t)&policy,
        THREAD_TIME_CONSTRAINT_POLICY_COUNT);
    return result == KERN_SUCCESS ? 1 : 0;
#else
    (void)computation_ns;
    (void)constraint_ns;
    struct sched_param param = {.sched_priority = sched_get_priority_min(SCHED_FIFO) + 10};
    return pthread_setschedparam(pthread_self(), SCHED_FIFO, &param) == 0 ? 1 : 0;
#endif
}
