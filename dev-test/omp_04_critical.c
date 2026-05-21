#include <stdio.h>
#include <omp.h>

#define N 1000

int main() {
    int i;
    int max_val = -1; // Issue: Race finding the maximum
    int data[N];

    for (i = 0; i < N; i++) {
        data[i] = i % 100; // max will be 99
    }

#pragma omp parallel for
    for (i = 0; i < N; i++) {
        if (data[i] > max_val) {
            // Race condition: multiple threads checking and writing max_val
            max_val = data[i];
        }
    }

    printf("Max value is: %d\n", max_val);
    return 0;
}
