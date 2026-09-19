const std = @import("std");

pub const Cart = struct {
    count: usize,

    pub fn add(self: *Cart) void {
        if (self.count < 10) self.count += 1;
    }
};

fn hidden() void {}
