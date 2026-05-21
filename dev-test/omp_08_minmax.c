#include <stdio.h>
#include <omp.h>
#include <math.h>

#define N 1000

int main() {
    int i;
    double min_dist = 999999.0;
    double max_dist = -999999.0;
    int points_processed = 0;
    
    double data_x[N];
    double data_y[N];

    for (i = 0; i < N; i++) {
        data_x[i] = i * 1.5;
        data_y[i] = i * 2.5;
    }

#pragma omp parallel for
    for (i = 0; i < N; i++) {
        double dist = sqrt(data_x[i] * data_x[i] + data_y[i] * data_y[i]);

        // Race: multiple threads updating min and max simultaneously
        if (dist < min_dist) {
            min_dist = dist;
        }
        if (dist > max_dist) {
            max_dist = dist;
        }

        // Race: counter increment
        points_processed++;
    }

    printf("Min: %f, Max: %f, Processed: %d\n", min_dist, max_dist, points_processed);
    return 0;
}
