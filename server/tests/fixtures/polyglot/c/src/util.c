#include "util.h"

int add(int a, int b) {
    if (a > 0 && b > 0) {
        return a + b;
    }
    return 0;
}

static int hidden(void) { return 1; }
