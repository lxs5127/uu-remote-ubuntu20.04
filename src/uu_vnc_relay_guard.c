#define _GNU_SOURCE
#include "x11_manager_capture_api.h"
#include <dlfcn.h>
#include <pthread.h>
#include <stdatomic.h>
#include <string.h>
#include <unistd.h>

static pthread_once_t once = PTHREAD_ONCE_INIT;
static int (*next_event)(MCDisplay *, MCEvent *);
static atomic_int entered;

static void initialize(void)
{
    void *symbol = dlsym(RTLD_NEXT, "XNextEvent");
    if (symbol == NULL || sizeof(symbol) != sizeof(next_event))
        _exit(70);
    memcpy(&next_event, &symbol, sizeof(next_event));
}

int XNextEvent(MCDisplay *display, MCEvent *event)
{
    pthread_once(&once, initialize);
    int result = next_event(display, event);
    /* TigerVNC 1.10 sends a final pointer position on FL_LEAVE. When the
     * redirected manager covers this relay, that old position is fed back
     * into the physical desktop. Loaded only by the private desktop relay.
     * Consume crossing events without blocking its event loop; keep all motion,
     * buttons, keyboard and connection events intact. */
    /* Let FLTK establish its initial viewport widget, then preserve that
     * association across manager cover/uncover. Otherwise it cannot dispatch
     * later genuine MotionNotify events to the remote desktop. */
    if (event->message.type == 8 ||
        (event->message.type == 7 && atomic_exchange(&entered, 1)))
        event->message.type = 0;
    return result;
}
