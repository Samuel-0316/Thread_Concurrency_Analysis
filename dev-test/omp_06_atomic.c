#include <stdio.h>
#include <omp.h>

#define N 500
#define NUM_BINS 10

int main() {
    int i;
    int data[N];
    int histogram[NUM_BINS] = {0};

    for (i = 0; i < N; i++) {
        data[i] = i % 100;
    }

#pragma omp parallel for
    for (i = 0; i < N; i++) {
        int bin = data[i] / 10;
        if (bin >= 0 && bin < NUM_BINS) {
            // Race: Multiple threads updating the same bin simultaneously
            histogram[bin]++;
        }
    }

    printf("Histogram Bin 0: %d\n", histogram[0]);
    return 0;
}
