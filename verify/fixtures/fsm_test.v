`timescale 1ns/1ps

module fsm_test;
    reg clk;
    reg rst_n;
    reg start;
    reg done;
    reg error;
    reg [2:0] state;
    reg [2:0] next_state;

    // State encoding
    localparam IDLE  = 3'b000;
    localparam START = 3'b001;
    localparam BUSY  = 3'b010;
    localparam WAIT  = 3'b011;
    localparam DONE  = 3'b100;
    localparam ERROR = 3'b111;

    initial begin
        $dumpfile("fsm_basic.vcd");
        $dumpvars(0, fsm_test);

        // Initialize
        clk = 0;
        rst_n = 0;
        start = 0;
        done = 0;
        error = 0;
        state = IDLE;

        // Reset
        #20 rst_n = 1;

        // Normal flow: IDLE -> START -> BUSY -> DONE -> IDLE
        #20 start = 1;
        #10 state = START;
        #10 start = 0;
        #10 state = BUSY;
        #50 state = DONE;
        #10 done = 1;
        #10 state = IDLE;
            done = 0;

        // Flow with wait: IDLE -> START -> BUSY -> WAIT -> BUSY -> DONE -> IDLE
        #20 start = 1;
        #10 state = START;
        #10 start = 0;
        #10 state = BUSY;
        #30 state = WAIT;
        #100 state = BUSY;  // Stuck in WAIT for 100ns
        #20 state = DONE;
        #10 done = 1;
        #10 state = IDLE;
            done = 0;

        // Error flow: IDLE -> START -> BUSY -> ERROR -> IDLE
        #20 start = 1;
        #10 state = START;
        #10 start = 0;
        #10 state = BUSY;
        #30 error = 1;
        #10 state = ERROR;
        #20 error = 0;
        #10 state = IDLE;

        // Stuck in BUSY (anomaly)
        #20 start = 1;
        #10 state = START;
        #10 start = 0;
        #10 state = BUSY;
        #200;  // Stuck for 200ns

        #50 $finish;
    end

    always #5 clk = ~clk;

endmodule
