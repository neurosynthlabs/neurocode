#include "shape.hpp"
#include <vector>

namespace geo {

double Shape::area() const {
    return 1.0;
}

double scale(double v) {
    return v > 0 ? v * 2 : 0;
}

}
