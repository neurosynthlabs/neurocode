pub struct Invoice {
    pub amount: u32,
}

pub trait Priced {
    fn price(&self) -> u32;
}

pub enum Status {
    Open,
    Paid,
}

impl Invoice {
    pub fn total(&self) -> u32 {
        if self.amount > 100 { self.amount * 2 } else { self.amount }
    }
}

fn private_helper() {}

pub const LIMIT: u32 = 10;
