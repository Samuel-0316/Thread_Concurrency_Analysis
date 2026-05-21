#include <stdio.h>
#include <omp.h>

#define N 100

int main() {
    int i, j;
    int matrix[N][N];
    int shared_temp = 0; // Issue: WAW inside nested loop

#pragma omp parallel for
    for (i = 0; i < N; i++) {
        for (j = 0; j < N; j++) {
            // Race: shared_temp overwritten by all threads simultaneously
            shared_temp = i * j;
            matrix[i][j] = shared_temp;
        }
    }

    printf("Done\n");
    return 0;
}
