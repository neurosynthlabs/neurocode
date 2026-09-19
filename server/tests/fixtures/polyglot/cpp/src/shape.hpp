#pragma once

namespace geo {

class Shape {
public:
    double area() const;
};

struct Point { double x, y; };

enum class Kind { Circle, Square };

}
