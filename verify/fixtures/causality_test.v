`timescale 1ns/1ps

module causality_test;
    reg clk;
    reg rst_n;

    // Input signals (potential causes)
    reg input_valid;
    reg fifo_write;
    reg fifo_full;
    reg [7:0] timeout_counter;

    // Output signal (effect)
    reg error_flag;
    reg busy;

    // Unrelated signals
    reg unrelated_signal;
    reg [3:0] counter;

    initial begin
        $dumpfile("causality_basic.vcd");
        $dumpvars(0, causality_test);

        // Initialize
        clk = 0;
        rst_n = 0;
        input_valid = 0;
        fifo_write = 0;
        fifo_full = 0;
        timeout_counter = 0;
        error_flag = 0;
        busy = 0;
        unrelated_signal = 0;
        counter = 0;

        // Reset
        #20 rst_n = 1;

        // ============================================
        // Pattern 1: fifo_full -> error_flag (5 times)
        // This establishes a high correlation
        // ============================================
        #20 input_valid = 1;
            fifo_write = 1;
        #10 fifo_full = 1;
        #5  error_flag = 1;     // 5ns after fifo_full
        #10 error_flag = 0;
            fifo_full = 0;
            input_valid = 0;
            fifo_write = 0;

        #30 input_valid = 1;
            fifo_write = 1;
        #15 fifo_full = 1;
        #5  error_flag = 1;
        #10 error_flag = 0;
            fifo_full = 0;
            input_valid = 0;
            fifo_write = 0;

        #30 input_valid = 1;
            fifo_write = 1;
        #20 fifo_full = 1;
        #5  error_flag = 1;
        #10 error_flag = 0;
            fifo_full = 0;
            input_valid = 0;
            fifo_write = 0;

        #30 input_valid = 1;
            fifo_write = 1;
        #10 fifo_full = 1;
        #5  error_flag = 1;
        #10 error_flag = 0;
            fifo_full = 0;
            input_valid = 0;
            fifo_write = 0;

        // ============================================
        // Some unrelated activity (noise)
        // ============================================
        #30 unrelated_signal = 1;
        #20 counter = 1;
        #20 counter = 2;
        #20 unrelated_signal = 0;
        #20 counter = 3;

        // ============================================
        // Pattern 2: The "target" event we want to analyze
        // At T=500ns, error_flag goes high
        // Just before, fifo_full and timeout_counter changed
        // ============================================
        #20 busy = 1;                  // T=470ns
        #10 timeout_counter = 100;     // T=480ns
        #10 fifo_full = 1;             // T=490ns
        #10 error_flag = 1;            // T=500ns (THE EFFECT WE INVESTIGATE)
        #20 error_flag = 0;
            fifo_full = 0;
            busy = 0;
            timeout_counter = 0;

        #50 $finish;
    end

    always #5 clk = ~clk;

endmodule
