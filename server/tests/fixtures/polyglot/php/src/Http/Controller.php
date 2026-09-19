<?php

namespace App\Http;

use App\Models\User;
use Illuminate\Http\Request;

class Controller
{
    public function show(): string
    {
        return (new User())->name();
    }
}
