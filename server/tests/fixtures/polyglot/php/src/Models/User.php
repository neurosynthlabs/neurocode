<?php

namespace App\Models;

class User
{
    public function name(): string
    {
        return 'x';
    }
}

interface Named {}

trait HasName {}

function helper() {}
