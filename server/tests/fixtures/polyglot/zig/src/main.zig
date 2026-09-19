const std = @import("std");
const cart = @import("cart.zig");

pub fn main() void {
    var c = cart.Cart{ .count = 0 };
    c.add();
}
